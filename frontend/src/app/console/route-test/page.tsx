import { Suspense } from "react";
import RouteTest from "../RouteTest";

export default function RouteTestPage() {
  return <Suspense fallback={<div className="text-gray-400 text-sm">加载…</div>}><RouteTest /></Suspense>;
}
