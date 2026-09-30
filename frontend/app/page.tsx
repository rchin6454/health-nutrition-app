import ChatPanel from "@/components/ChatPanel";
import SourcesPanel from "@/components/SourcesPanel";

export default function Home() {
  return (
    <div className="flex h-dvh flex-col">
      <header className="border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
        <h1 className="text-lg font-semibold">
          Food &amp; Nutrition Assistant
        </h1>
      </header>
      <main className="flex min-h-0 flex-1 flex-col gap-4 p-4 md:flex-row">
        <ChatPanel />
        <SourcesPanel />
      </main>
      <footer className="px-4 pb-3 text-center text-xs text-zinc-500">
        General information, not medical advice.
      </footer>
    </div>
  );
}
