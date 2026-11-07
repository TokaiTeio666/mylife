"""进程级运行时元数据。"""

from __future__ import annotations

import time

#: SSE epoch：进程启动即变；客户端 epoch 不匹配时从 run 状态重建。
STREAM_EPOCH: int = int(time.time()) & 0x7FFFFFFF
