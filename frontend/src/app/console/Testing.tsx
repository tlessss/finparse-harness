"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import RecallTest from "./RecallTest";
import RouteTest from "./RouteTest";
import ParseTest from "./ParseTest";
import ColumnsTest from "./ColumnsTest";
import JudgeTest from "./JudgeTest";
import VerifyTest from "./VerifyTest";
import CommitReview from "./CommitReview";
import CommittedList from "./CommittedList";
import HealTest from "./HealTest";
import TestData from "./TestData";

const TABS = [
  { key: "recall", label: "① 选表解耦", desc: "向量召回+锚精判(唯一选表)" },
  { key: "route", label: "② 路由", desc: "指纹命中哪个解析器插件" },
  { key: "parse", label: "③ 解析", desc: "选中表→结构化,对锚" },
  { key: "columns", label: "🔤 认列", desc: "判名称/金额/占比列" },
  { key: "judge", label: "④ 诊断agent", desc: "锚没过→找病根(吵,慎用)" },
  { key: "verify", label: "✅ 复核agent", desc: "锚过→审盲区,pass才过" },
  { key: "commit", label: "⑤ 入库审核", desc: "人通过→写库(当前测试库)" },
  { key: "committed", label: "📥 已入库", desc: "复核通过写入的数据" },
  { key: "heal", label: "⑥ 自愈", desc: "真失败筛子→病历" },
  { key: "data", label: "📊 测试数据", desc: "回看 + 标对错" },
] as const;

export default function Testing() {
  const params = useParams();
  const sp = useSearchParams();
  const router = useRouter();
  const active = (params?.stage as string) || "recall";
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
        {active === "recall" && <RecallTest initial={shared} />}
        {active === "route" && <RouteTest initial={shared} onNext={(s) => go("parse", s)} />}
        {active === "parse" && <ParseTest initial={shared} onNext={(s) => go("judge", s)} />}
        {active === "columns" && <ColumnsTest initial={shared} />}
        {active === "judge" && <JudgeTest initial={shared} />}
        {active === "verify" && <VerifyTest initial={shared} />}
        {active === "commit" && <CommitReview />}
        {active === "committed" && <CommittedList />}
        {active === "heal" && <HealTest initial={shared} />}
        {active === "data" && <TestData />}
      </main>
    </div>
  );
}
