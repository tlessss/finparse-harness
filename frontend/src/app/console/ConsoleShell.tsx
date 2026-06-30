"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/console/triage", label: "分诊队列" },
  { href: "/console/review", label: "审核认证关" },
  { href: "/console/control", label: "批量控制" },
  { href: "/console/testing", label: "测试" },
  { href: "/console/workflow", label: "工作流" },
] as const;

export default function ConsoleShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

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
          <span className="text-xs text-gray-400">正式批量跑前的人在回路闸门</span>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6">{children}</main>
    </div>
  );
}
