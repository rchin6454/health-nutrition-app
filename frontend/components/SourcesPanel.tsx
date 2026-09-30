// Always empty by design: every claim's `source` is null, so there is nothing to list.
// It takes no props, so it can't accidentally render sources, passages or dataset names.
export default function SourcesPanel() {
  return (
    <aside
      aria-labelledby="sources-heading"
      className="flex flex-col rounded-xl border border-zinc-200 bg-white p-4 md:w-72 md:shrink-0 dark:border-zinc-800 dark:bg-zinc-900"
    >
      <h2
        id="sources-heading"
        className="text-sm font-semibold text-zinc-700 dark:text-zinc-300"
      >
        Sources
      </h2>
      <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
        No sources to show.
      </p>
    </aside>
  );
}
