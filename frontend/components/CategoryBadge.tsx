import type { ChatResponse } from "@/lib/types";

type Badge = { label: string; className: string };

const BADGES = {
  nutrition: {
    label: "Nutrition",
    className:
      "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  },
  food_safety: {
    label: "Food Safety",
    className:
      "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  },
  general_food: {
    label: "General Food",
    className: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  },
  mixed: {
    label: "Nutrition + Food Safety",
    className: "bg-teal-100 text-teal-800 dark:bg-teal-950 dark:text-teal-300",
  },
  clarification: {
    label: "Needs more detail",
    className:
      "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
  },
  out_of_scope: {
    label: "Out of scope",
    className: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  },
  error: {
    label: "Error",
    className: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  },
} satisfies Record<string, Badge>;

function badgeFor(response: ChatResponse): Badge | null {
  if (response.answer_type !== "answer") return BADGES[response.answer_type];
  // Answers are labelled by the category the backend's understanding step set.
  return response.category === "none" || response.category === "out_of_scope"
    ? null
    : BADGES[response.category];
}

export default function CategoryBadge({
  response,
}: {
  response: ChatResponse;
}) {
  const badge = badgeFor(response);
  if (!badge) return null;
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${badge.className}`}
    >
      {badge.label}
    </span>
  );
}
