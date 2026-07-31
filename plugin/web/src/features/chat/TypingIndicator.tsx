import { useEffect, useRef, useState } from "react";

export function TypingIndicator() {
  return (
    <div className="flex justify-start gap-3 py-2" aria-live="polite">
      <div className="rounded-2xl rounded-bl-md bg-card px-4 py-3 shadow-panel">
        <div className="flex items-center gap-1">
          <Dot delay={0} />
          <Dot delay={150} />
          <Dot delay={300} />
          <span className="sr-only">AI antwortet…</span>
        </div>
      </div>
    </div>
  );
}

// Haelt den Indicator beim Ausblenden noch ~160ms gemountet, damit er weich
// ausfaden kann, statt hart zu verschwinden, waehrend die Antwort-Bubble
// erscheint. Einblendung faded ebenfalls (statt Pop-in zwischen Tool-Runden).
export function TypingIndicatorPresence({ show }: { show: boolean }) {
  const [mounted, setMounted] = useState(show);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current);
    if (show) {
      setMounted(true);
      return;
    }
    timer.current = window.setTimeout(() => setMounted(false), 160);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [show]);

  if (!mounted) return null;
  return (
    <div
      className={
        show
          ? "animate-in fade-in-0 duration-200 motion-reduce:animate-none"
          : "animate-out fade-out-0 fill-mode-forwards duration-150 motion-reduce:animate-none"
      }
    >
      <TypingIndicator />
    </div>
  );
}

function Dot({ delay }: { delay: number }) {
  return (
    <span
      className="h-1.5 w-1.5 animate-typing-dot rounded-full bg-muted-foreground/80"
      style={{ animationDelay: `${delay}ms` }}
    />
  );
}
