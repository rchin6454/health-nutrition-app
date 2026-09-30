// The example questions from the problem statement.
export const SUGGESTED_PROMPTS = [
  "Is brown rice healthier than white rice?",
  "How much protein is there in 100g of paneer?",
  "Can I eat cooked rice that was left outside overnight?",
  "What foods are high in iron?",
  "How long can chicken be stored in the refrigerator?",
];

type Props = {
  onSelect: (prompt: string) => void;
  disabled?: boolean;
};

export default function SuggestedPrompts({ onSelect, disabled }: Props) {
  return (
    <div className="flex flex-col items-center gap-4 py-8 text-center">
      <p className="text-zinc-600 dark:text-zinc-400">
        Ask about food, nutrition or food safety. Try one of these:
      </p>
      <ul className="flex max-w-xl flex-wrap justify-center gap-2">
        {SUGGESTED_PROMPTS.map((prompt) => (
          <li key={prompt}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onSelect(prompt)}
              className="rounded-full border border-zinc-300 px-3 py-1.5 text-sm text-zinc-700 hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-emerald-600 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              {prompt}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
