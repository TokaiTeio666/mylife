"use client";

import * as React from "react";

/** 生产环境注册 Service Worker；开发环境跳过以免缓存干扰热更新。 */
export function SwRegister() {
  React.useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // 离线壳是增强项，注册失败不影响主流程。
    });
  }, []);
  return null;
}
