"""独立 Embedding Server —— 用 transformers 加载 BGE-m3，暴露 /embed 和 /health。

兼容 TEI API 格式，TeiClient 无需修改即可调用。
零额外依赖：transformers + torch 已由 docling 引入。
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import torch
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from pydantic import BaseModel
from transformers import AutoModel, AutoTokenizer

logger = logging.getLogger("emily.embedding_server")

MODEL_ID = os.getenv("EMBEDDING_MODEL_ID", "BAAI/bge-m3")
PORT = int(os.getenv("EMBEDDING_PORT", "8000"))
MAX_LENGTH = int(os.getenv("EMBEDDING_MAX_LENGTH", "8192"))

_model: Optional[AutoModel] = None
_tokenizer: Optional[AutoTokenizer] = None
_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


class EmbedRequest(BaseModel):
    inputs: str | list[str]


def get_model_and_tokenizer() -> tuple[AutoModel, AutoTokenizer]:
    """懒加载 BGE-m3 模型（首次调用时下载并缓存到 HF_HOME）。"""
    global _model, _tokenizer
    if _model is None:
        logger.info(f"Loading embedding model: {MODEL_ID} (device={_device})")
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        _model = AutoModel.from_pretrained(MODEL_ID).to(_device)
        _model.eval()
        dim = _model.config.hidden_size
        logger.info(f"Embedding model loaded, dim={dim}")
    return _model, _tokenizer


def _mean_pooling(token_embeddings: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
    """对 token embeddings 做 attention-weighted mean pooling。

    等效于 sentence-transformers 的 mean pooling。
    """
    mask = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    summed = torch.sum(token_embeddings * mask, dim=1)
    counts = torch.clamp(mask.sum(dim=1), min=1e-9)
    return summed / counts


def _normalize(embeddings: torch.Tensor) -> torch.Tensor:
    """L2 normalize（BGE 系列模型预期输出归一化向量）。"""
    return torch.nn.functional.normalize(embeddings, p=2, dim=1)


def encode(texts: list[str]) -> torch.Tensor:
    """对文本列表生成 embedding 向量。

    与 sentence-transformers .encode() 行为一致。
    """
    if not texts:
        return torch.empty((0,))

    model, tokenizer = get_model_and_tokenizer()

    # 无 attention_mask 时 tokenizer 默认不返回额外列，需手动处理
    encoded = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=MAX_LENGTH,
        return_tensors="pt",
    )

    encoded = {k: v.to(_device) for k, v in encoded.items()}

    with torch.no_grad():
        outputs = model(**encoded)

    # BGE-m3 输出: last_hidden_state (CLS pooling 或 mean pooling 都可用)
    # BGE 官方建议对 [CLS] 做归一化，这里用 mean pooling + normalize
    embeddings = _mean_pooling(outputs.last_hidden_state, encoded["attention_mask"])
    embeddings = _normalize(embeddings)

    return embeddings.cpu()


app = FastAPI(title="Emily Embedding Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/embed")
async def embed(req: EmbedRequest):
    """生成密集向量，兼容 TEI /embed API。

    Request:  {"inputs": "text" | ["text1", "text2"]}
    Response: [[vec1], [vec2], ...]
    """
    inputs = req.inputs if isinstance(req.inputs, list) else [req.inputs]
    result = encode(inputs)
    return result.tolist()


@app.get("/health")
async def health():
    """健康检查，兼容 TEI /health API。"""
    return {"status": "ok"}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    # 启动时预热模型（会在首次推理时下载模型）   
    logger.info(f"Starting embedding server on 0.0.0.0:{PORT}")
    get_model_and_tokenizer()
    uvicorn.run(app, host="0.0.0.0", port=PORT)
