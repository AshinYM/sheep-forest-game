"""Real LangGraph routing. Canonical state/request durability remains in SQLite."""
from typing import TypedDict, Annotated
import operator
from langgraph.graph import StateGraph, START, END
from langgraph.types import RetryPolicy
from langsmith import tracing_context
from .domain import Action, GameEngine, GameState
from .interpret import Interpretation, local_parse


class TurnContext(TypedDict, total=False):
    token: str
    payload: dict
    world: GameState
    interpretation: Interpretation
    action: Action
    route: str
    result: dict
    new_world: GameState
    visited: Annotated[list[str], operator.add]


def build_workflow(service):
    def route_request(ctx):
        route = 'ended' if ctx['world'].game_over else 'resolve_confirmation' if ctx['payload']['operation']!='input' else 'interpret_local'
        return {'route':route,'visited':['route_request']}

    def ended(ctx):
        return {'result':GameEngine(ctx['world']).apply(Action('wait')).public(), 'new_world':ctx['world'],'visited':['ended']}

    def interpret_local(ctx):
        return {'interpretation':local_parse(ctx['payload']['text'],ctx['world']),'visited':['interpret_local']}

    def after_local(ctx):
        return 'interpret_model' if ctx['interpretation'].kind=='ambiguous' and service.interpreter else 'route_intent'

    def interpret_model(ctx):
        service._reserve_call(ctx['token'])
        result=service.interpreter.interpret(ctx['payload']['text'],ctx['world'])
        return {'interpretation':result,'visited':['interpret_model']}

    def route_intent(ctx):
        interpreted=ctx['interpretation']
        route='no_action' if interpreted.kind!='action' else 'propose_action' if interpreted.source=='openai' else 'execute_rules'
        return {'route':route,'action':interpreted.action,'visited':['route_intent']}

    def no_action(ctx):
        kind=ctx['interpretation'].kind
        message=('네 목소리가 나무 사이로 사라졌다. 괜찮다면 다음에 할 행동을 말해 줘.' if kind=='dialogue'
                 else '뜻을 확실히 알 수 없습니다. 한 번에 행동 하나와 대상을 말해 주세요. 위반과 시간은 늘지 않았습니다.')
        return {'result':{'status':kind,'message':message,'hint':None},'new_world':ctx['world'],'visited':['no_action']}

    def propose_action(ctx):
        return {'result':service._propose(ctx['token'],ctx['world'],ctx['action']),
                'new_world':ctx['world'],'visited':['propose_action']}

    def resolve_confirmation(ctx):
        action=service._resolve_proposal(ctx['token'],ctx['payload'],ctx['world'])
        return {'action':action,'route':'cancelled' if ctx['payload']['operation']=='cancel' else 'execute_rules','visited':['resolve_confirmation']}

    def cancelled(ctx):
        return {'result':{'status':'cancelled','message':'실행하지 않았습니다. 의도한 행동을 다시 말해 주세요.','hint':None},
                'new_world':ctx['world'],'visited':['cancelled']}

    def execute_rules(ctx):
        result=GameEngine(ctx['world']).apply(ctx['action'])
        return {'result':result.public(),'new_world':result.state,'visited':['execute_rules']}

    graph=StateGraph(TurnContext)
    nodes={'route_request':route_request,'ended':ended,'interpret_local':interpret_local,'interpret_model':interpret_model,
           'route_intent':route_intent,'no_action':no_action,'propose_action':propose_action,
           'resolve_confirmation':resolve_confirmation,'cancelled':cancelled,'execute_rules':execute_rules}
    for name,node in nodes.items():
        graph.add_node(name,node,retry_policy=RetryPolicy(max_attempts=1))
    graph.add_edge(START,'route_request')
    graph.add_conditional_edges('route_request',lambda ctx:ctx['route'],['ended','resolve_confirmation','interpret_local'])
    graph.add_conditional_edges('interpret_local',after_local,['interpret_model','route_intent'])
    graph.add_edge('interpret_model','route_intent')
    graph.add_conditional_edges('route_intent',lambda ctx:ctx['route'],['no_action','propose_action','execute_rules'])
    graph.add_conditional_edges('resolve_confirmation',lambda ctx:ctx['route'],['cancelled','execute_rules'])
    for name in ['ended','no_action','propose_action','cancelled','execute_rules']: graph.add_edge(name,END)
    # No remote store/checkpointer. Re-enter through the durable service request
    # boundary on confirmation, rather than replaying an LLM node on graph resume.
    return graph.compile()


def invoke(workflow, token, payload, state):
    with tracing_context(enabled=False):
        output=workflow.invoke({'token':token,'payload':payload,'world':state,'visited':[]},config={'callbacks':[],'recursion_limit':12})
    return output
