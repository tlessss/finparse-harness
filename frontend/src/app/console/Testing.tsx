"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import SelectTest from "./SelectTest";
import RouteTest from "./RouteTest";
import ParseTest from "./ParseTest";
import ColumnsTest from "./ColumnsTest";
import JudgeTest from "./JudgeTest";
import CommitReview from "./CommitReview";
import HealTest from "./HealTest";
import TestData from "./TestData";
import RevenueParserViz from "./RevenueParserViz";

const TABS = [
  { key: "select", label: "① 选表测试", desc: "filter 选得准不准" },
  { key: "route", label: "② 路由测试", desc: "指纹命中哪个解析器" },
  { key: "parse", label: "③ 解析测试", desc: "冷启动对不对锚" },
  { key: "columns", label: "🔤 认列测试", desc: "判名称/金额/占比列" },
  { key: "revenue-viz", label: "📐 营收解析器", desc: "逻辑流程 + mock 数据" },
  { key: "judge", label: "④ LLM判定", desc: "末道诊断(吵,慎用)" },
  { key: "commit", label: "⑤ 入库审核", desc: "人通过→写生产库" },
  { key: "heal", label: "⑥ 自愈", desc: "真失败筛子→病历" },
  { key: "data", label: "📊 测试数据", desc: "回看 + 标对错" },
] as const;

export default function Testing() {
  const params = useParams();
  const sp = useSearchParams();
  const router = useRouter();
  const active = (params?.stage as string) || "select";
  const shared = {
    code: sp.get("code") || "000333",
    year: Number(sp.get("year")) || 2025,
    field: sp.get("field") || "revenue_breakdown",
  };
  // 跳到某子路由,带上当前 code/year/field(刷新/前进后退都保留)
  const go = (stage: string, s = shared) =>
    router.push(`/console/testing/${stage}?code=${s.code}&year=${s.year}&field=${s.field}`);

  return (
    <div className="flex gap-4 items-start">
      <aside className="w-44 shrink-0">
        <div className="bg-white rounded-lg shadow-sm border p-2 sticky top-20">
          <div className="text-xs text-gray-400 px-2 py-1">测试阶段</div>
          {TABS.map((t) => (
            <button key={t.key} onClick={() => go(t.key)}
              className={`w-full text-left px-3 py-2 rounded mb-1 transition ${active === t.key ? "bg-blue-600 text-white" : "hover:bg-gray-100 text-gray-700"}`}>
              <div className="text-sm font-medium">{t.label}</div>
              <div className={`text-[10px] ${active === t.key ? "text-blue-100" : "text-gray-400"}`}>{t.desc}</div>
            </button>
          ))}
        </div>
      </aside>
      <main className="flex-1 min-w-0">
        {active === "select" && <SelectTest initial={shared} onConfirm={(s) => go("route", s)} />}
        {active === "route" && <RouteTest initial={shared} onNext={(s) => go("parse", s)} />}
        {active === "parse" && <ParseTest initial={shared} onNext={(s) => go("judge", s)} />}
        {active === "columns" && <ColumnsTest initial={shared} />}
        {active === "revenue-viz" && <RevenueParserViz />}
        {active === "judge" && <JudgeTest initial={shared} />}
        {active === "commit" && <CommitReview />}
        {active === "heal" && <HealTest initial={shared} />}
        {active === "data" && <TestData />}
      </main>
    </div>
  );
}
