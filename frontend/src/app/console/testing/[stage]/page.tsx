import { Suspense } from "react";
import Testing from "../../Testing";

export default function TestingStagePage() {
  return <Suspense fallback={<div className="text-gray-400 text-sm">加载…</div>}><Testing /></Suspense>;
}
