# coding: utf-8
"""
兼容层：统一复用 core/server/engines/llama 的运行库绑定。

本目录曾各带一份 llama.py 副本，各自去加载自己 bin/ 目录下的运行库，
导致 GGUF 引擎在缺少该目录时导入即崩溃，且副本 ABI 落后于
llama.cpp b10621。这里只做转发，保留模块路径以兼容 `from . import llama`。
"""

from ...llama.llama import (  # noqa: F401
    ASRStreamDecoder,
    LlamaBatch,
    LlamaContext,
    LlamaEmbeddingTable,
    LlamaModel,
    LlamaSampler,
    bind_llama_lib,
    get_one_batch,
    get_token_embeddings_gguf,
    init,
    llama_token,
    logger,
    text_to_tokens,
    token_to_bytes,
)

__all__ = [
    "ASRStreamDecoder",
    "LlamaBatch",
    "LlamaContext",
    "LlamaEmbeddingTable",
    "LlamaModel",
    "LlamaSampler",
    "bind_llama_lib",
    "get_one_batch",
    "get_token_embeddings_gguf",
    "init",
    "llama_token",
    "logger",
    "text_to_tokens",
    "token_to_bytes",
]
