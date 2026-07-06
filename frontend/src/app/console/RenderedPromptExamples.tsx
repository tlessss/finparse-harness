"use client";

import { useState } from "react";
import verifyExamples from "@/data/verifyPromptExamples.json";

type Message = { role: string; content: string };
type Example = {
  id: string;
  title: string;
  scenario: string;
  expected_verdict: string;
  expected_issue: string | null;
  pipeline: string;
  messages: Message[];
  expected_reply?: Record<string, unknown>;
};

const DATA = verifyExamples as {
  agent_id: string;
  note: string;
  examples: Example[];
};

function verdictBadge(v: string) {
  if (v === "pass") return "text-emerald-700 bg-emerald-50";
  if (v === "hold") return "text-red-700 bg-red-50";
  return "text-gray-600 bg-gray-100";
}

export default function RenderedPromptExamples({ agentId }: { agentId: string }) {
  const [openId, setOpenId] = useState<string | null>(null);
  if (agentId !== DATA.agent_id || !DATA.examples.length) return null;

  return (
    <div className="mt-3 border-t pt-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-gray-600">完整 Prompt 样例 · v2 mock</span>
        <span className="text-[10px] text-violet-600 font-mono">设计稿</span>
      </div>
      <p className="text-[10px] text-gray-400 mt-0.5 leading-snug">
        渲染后 messages，无 {"{{变量}}"}；来源 docs/mock-verify-prompt-v2.md §三-B
      </p>
      <ul className="mt-2 space-y-1.5">
        {DATA.examples.map((ex) => {
          const open = openId === ex.id;
          return (
            <li key={ex.id} className="border rounded overflow-hidden bg-white">
              <button
                type="button"
                onClick={() => setOpenId(open ? null : ex.id)}
                className="w-full text-left px-2 py-1.5 flex items-start justify-between gap-2 hover:bg-gray-50"
              >
                <span className="text-[11px] leading-snug min-w-0">
                  <span className="font-mono font-semibold text-gray-800">{ex.id}</span>
                  <span className="text-gray-600"> · {ex.title}</span>
                  <span className={`ml-1 inline-block px-1 py-px rounded text-[10px] font-medium ${verdictBadge(ex.expected_verdict)}`}>
                    {ex.expected_verdict}
                  </span>
                  {ex.expected_issue && (
                    <span className="ml-1 text-[10px] text-amber-700">{ex.expected_issue}</span>
                  )}
                </span>
                <span className="text-gray-400 text-xs shrink-0 mt-0.5">{open ? "▾" : "▸"}</span>
              </button>
              {open && (
                <div className="px-2 pb-2 border-t bg-gray-50/50">
                  <p className="text-[10px] text-gray-500 mt-1.5 leading-snug">
                    {ex.scenario} → pipeline: <span className="font-mono">{ex.pipeline}</span>
                  </p>
                  {ex.messages.map((m) => (
                    <div key={m.role}>
                      <div className="text-[10px] text-gray-400 mt-2 font-medium">{m.role}（最终发给 LLM）</div>
                      <pre className="text-[10px] leading-snug whitespace-pre-wrap break-words bg-white border rounded p-2 mt-0.5 max-h-72 overflow-auto text-gray-700">
                        {m.content}
                      </pre>
                    </div>
                  ))}
                  {ex.expected_reply && (
                    <>
                      <div className="text-[10px] text-gray-400 mt-2 font-medium">期望 LLM 回复（金标准）</div>
                      <pre className="text-[10px] leading-snug whitespace-pre-wrap break-words bg-amber-50 border border-amber-100 rounded p-2 mt-0.5 max-h-40 overflow-auto text-gray-700">
                        {JSON.stringify(ex.expected_reply, null, 2)}
                      </pre>
                    </>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
