import { useEffect, useRef, useState } from "react";
import { ChevronDown, Circle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { GhState } from "@/hooks/useGrasshopperStatus";

type Props = {
  backendConnected: boolean;
  ghState: GhState;
  connectGrasshopper: () => Promise<void>;
};

export function SystemStatusIndicator({
  backendConnected,
  ghState,
  connectGrasshopper,
}: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const backendState = backendConnected ? "ready" : "disconnected";
  const ghIndicatorState = ghState === "connected" ? "ready" : ghState;
  const title = `Systemstatus: Plugin ${backendConnected ? "verbunden" : "getrennt"}, Grasshopper ${ghLabel(ghState)}`;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-label={title}
        title={title}
        className={cn(
          "flex h-7 cursor-pointer items-center gap-1.5 rounded-full px-2.5 shadow-sm transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
          backendConnected && ghState === "connected"
            ? "bg-success-soft text-success hover:bg-success-soft/70"
            : "bg-brand-orange-soft text-brand-orange hover:bg-brand-orange-soft/70",
        )}
      >
        <span className="flex items-center gap-1" aria-hidden="true">
          <Circle
            className={cn("h-2.5 w-2.5", statusDotClass(backendState))}
          />
          <Circle
            className={cn("h-2.5 w-2.5", statusDotClass(ghIndicatorState))}
          />
        </span>
        <ChevronDown
          className={cn(
            "h-3 w-3 transition-transform",
            open ? "rotate-180" : "",
          )}
        />
      </button>

      {open ? (
        <div className="absolute left-0 top-10 z-[80] w-60 rounded-2xl bg-card p-4 text-xs text-card-foreground shadow-pop">
          <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Systemstatus
          </div>
          <div className="space-y-2.5">
            <StatusRow
              label="Plugin"
              value={backendConnected ? "Verbunden" : "Keine Verbindung"}
              state={backendConnected ? "ready" : "disconnected"}
            />
            <StatusRow
              label="Grasshopper"
              value={ghLabel(ghState)}
              state={ghState === "connected" ? "ready" : ghState}
            />
          </div>
          {ghState !== "connected" ? (
            <button
              type="button"
              onClick={connectGrasshopper}
              disabled={ghState === "connecting"}
              className="mt-3 w-full cursor-pointer rounded-xl bg-primary px-3 py-2 text-center text-xs font-semibold text-primary-foreground transition-colors duration-200 hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-default disabled:opacity-60"
            >
              {ghState === "connecting"
                ? "Grasshopper wird gestartet…"
                : "Grasshopper verbinden"}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function StatusRow({
  label,
  value,
  state,
}: {
  label: string;
  value: string;
  state: "ready" | "connecting" | "disconnected" | "unknown";
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="flex items-center gap-2">
        <Circle className={cn("h-2 w-2", statusDotClass(state))} />
        <span className="font-medium">{label}</span>
      </div>
      <span
        className={cn(
          "rounded-full px-2 py-0.5 text-[11px] font-medium",
          statusChipClass(state),
        )}
      >
        {value}
      </span>
    </div>
  );
}

function ghLabel(state: GhState): string {
  if (state === "connected") return "verbunden";
  if (state === "connecting") return "startet";
  return "nicht verbunden";
}

function statusDotClass(
  state: "ready" | "connecting" | "disconnected" | "unknown",
): string {
  if (state === "ready") return "fill-success text-success";
  if (state === "connecting")
    return "fill-brand-orange text-brand-orange animate-pulse";
  if (state === "disconnected") return "fill-brand-orange text-brand-orange";
  return "fill-muted-foreground text-muted-foreground";
}

function statusChipClass(
  state: "ready" | "connecting" | "disconnected" | "unknown",
): string {
  // -deep-Texttöne: die leuchtenden Grundfarben schaffen auf den
  // Soft-Flächen nur ~3:1 — zu wenig für 11px-Text (WCAG AA).
  if (state === "ready") return "bg-success-soft text-success-deep";
  if (state === "connecting")
    return "bg-brand-orange-soft text-brand-orange-deep animate-pulse";
  if (state === "disconnected")
    return "bg-brand-orange-soft text-brand-orange-deep";
  return "bg-muted text-muted-foreground";
}
