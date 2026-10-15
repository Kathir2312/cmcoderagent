"""The model layer of the Phase 8 engine, on LangChain (docs/project/phases/phase8/PLAN.md).

chat_openai.py: LangChain's ChatOpenAI with cmcoder's fixes for its gateways
and for Qwen on vLLM. factory.py: builds it from cmcoder's settings, with
cmcoder's TLS clients and the key from the keychain.
"""
