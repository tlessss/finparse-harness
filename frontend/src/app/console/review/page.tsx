"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import ReviewPanel from "../ReviewPanel";

function ReviewPageContent() {
  const sp = useSearchParams();
  const code = sp.get("code") || undefined;
  const yearRaw = sp.get("year");
  const year = yearRaw ? Number(yearRaw) : undefined;
  const field = sp.get("field") || undefined;

  return <ReviewPanel code={code} year={year} field={field} />;
}

export default function ReviewPage() {
  return (
    <Suspense fallback={<div className="text-center text-gray-400 py-10">加载审核任务…</div>}>
      <ReviewPageContent />
    </Suspense>
  );
}
