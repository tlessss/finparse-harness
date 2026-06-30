// 统一 API 调用：真接口优先，失败(后端没起/旧实例/无该路由)回退到 mock。
// 返回 {data, live}：live=true 表示来自真后端，false=用了 mock。UI 据此显示"实时/示例"角标。
export const API_BASE = "http://localhost:8200";

export async function apiGet<T>(path: string, fallback: T): Promise<{ data: T; live: boolean }> {
  try {
    const r = await fetch(`${API_BASE}${path}`);
    if (!r.ok) throw new Error(String(r.status));
    return { data: (await r.json()) as T, live: true };
  } catch {
    return { data: fallback, live: false };
  }
}

export async function apiPost<T>(path: string, body: unknown, fallback: T): Promise<{ data: T; live: boolean }> {
  try {
    const r = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(String(r.status));
    return { data: (await r.json()) as T, live: true };
  } catch {
    return { data: fallback, live: false };
  }
}

// 小角标：实时(绿) / 示例(灰)
export function liveLabel(live: boolean): { text: string; cls: string } {
  return live
    ? { text: "● 实时", cls: "text-green-600" }
    : { text: "○ 示例数据(后端未就绪)", cls: "text-gray-400" };
}
