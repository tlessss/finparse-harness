"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { apiGet } from "./api";

const NAV = [
  { href: "/console/triage", label: "分诊队列" },
  { href: "/console/review", label: "审核认证关" },
  { href: "/console/control", label: "批量控制" },
  { href: "/console/testing", label: "测试" },
  { href: "/console/workflow", label: "工作流" },
  { href: "/console/pipeline", label: "流水线成功率" },
  { href: "/console/agents", label: "Agent 管理" },
] as const;

export default function ConsoleShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [db, setDb] = useState<{ reports_table?: string; is_test?: boolean } | null>(null);

  useEffect(() => {
    apiGet<{ reports_table?: string; is_test?: boolean } | null>("/health", null)
      .then(({ data, live }) => { if (live) setDb(data); });
  }, []);

  return (
    <div className="min-h-screen bg-gray-50 text-gray-800">
      <header className="bg-white border-b shadow-sm sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <Link href="/console/triage" className="text-xl font-bold hover:text-blue-700">
            FinParseAI · 控制 / 审核台
          </Link>
          <nav className="flex gap-2">
            {NAV.map(({ href, label }) => {
              const active = pathname === href || pathname.startsWith(`${href}/`);
              return (
                <Link
                  key={href}
                  href={href}
                  className={`px-4 py-1.5 rounded text-sm font-medium transition ${
                    active ? "bg-blue-600 text-white" : "text-gray-600 hover:bg-gray-100"
                  }`}
                >
                  {label}
                </Link>
              );
            })}
          </nav>
          <div className="flex items-center gap-3">
            {db && (
              <span title={`当前后端读写的报告表：${db.reports_table}`}
                className={`px-2.5 py-1 rounded text-xs font-semibold border ${
                  db.is_test
                    ? "bg-green-50 text-green-700 border-green-300"
                    : "bg-red-100 text-red-700 border-red-300 animate-pulse"}`}>
                {db.is_test ? "🧪 测试库" : "⚠ 生产库"} · {db.reports_table}
              </span>
            )}
            <span className="text-xs text-gray-400">正式批量跑前的人在回路闸门</span>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6">{children}</main>
    </div>
  );
}
