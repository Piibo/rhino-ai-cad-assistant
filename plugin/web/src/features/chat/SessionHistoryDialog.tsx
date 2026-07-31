import { useState } from "react";
import { Check, Copy, Download } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { api } from "@/lib/api";
import { useChatStore } from "@/store/chatStore";
import { cn } from "@/lib/utils";
import type { ExportResponse, Session } from "@/lib/types";

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}

export function SessionHistoryDialog({ open, onOpenChange }: Props) {
  const sessions = useChatStore((s) => s.sessions);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const setActiveSession = useChatStore((s) => s.setActiveSession);
  const [copyingId, setCopyingId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [copyError, setCopyError] = useState<string | null>(null);
  const [exportingId, setExportingId] = useState<string | null>(null);
  const [exportResult, setExportResult] = useState<ExportResponse | null>(null);

  const switchSession = (sessionId: string) => {
    setActiveSession(sessionId);
    onOpenChange(false);
  };

  // Export-Bundle einer Studien-Session (erneut) erzeugen. Das ist der
  // Retry-Pfad, falls der automatische Export nach dem Fragebogen
  // fehlschlug (Spec §2.6) — und der Weg, nach der Studie alle Bundles
  // frisch zu ziehen. Funktioniert auch für beendete Läufe.
  const exportStudyBundle = async (session: Session) => {
    if (exportingId) return;
    setExportingId(session.id);
    setCopyError(null);
    setExportResult(null);
    try {
      const study = await api.getStudySessionBySession(session.id);
      if (!study) {
        throw new Error("Keine Studien-Session zu dieser Sitzung gefunden.");
      }
      const result = await api.exportStudySession(study.id);
      setExportResult(result);
    } catch (err) {
      setCopyError(
        `Export fehlgeschlagen: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setExportingId(null);
    }
  };

  const copySessionDebugDump = async (session: Session) => {
    if (copyingId) return;
    setCopyingId(session.id);
    setCopyError(null);
    try {
      const dump = await api.getSessionDebugDump(session.id);
      await writeClipboard(dump.text);
      setCopiedId(session.id);
      window.setTimeout(() => {
        setCopiedId((current) => (current === session.id ? null : current));
      }, 1800);
    } catch (err) {
      setCopyError(
        `Kopieren fehlgeschlagen: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setCopyingId(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Frühere Sitzungen</DialogTitle>
          <DialogDescription>
            {sessions.length === 0
              ? "Noch keine Sitzungen gespeichert."
              : `${sessions.length} Sitzung${sessions.length === 1 ? "" : "en"} verfügbar.`}
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className="max-h-[60vh] pr-2">
          <div className="space-y-2 p-1">
            {sessions.map((session) => {
              const isActive = session.id === activeSessionId;
              return (
                <div
                  key={session.id}
                  className={cn(
                    "flex items-center gap-1 rounded-xl p-2 transition-colors duration-200",
                    isActive
                      ? "bg-primary/5 ring-2 ring-primary/40"
                      : "hover:bg-accent",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => switchSession(session.id)}
                    title={`${sessionLabel(session)} wieder öffnen`}
                    className="min-w-0 flex-1 cursor-pointer rounded-lg px-1.5 py-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">
                          {sessionLabel(session)}
                        </div>
                        <div className="truncate text-xs text-muted-foreground">
                          {formatDateTime(session.created_at)}
                        </div>
                      </div>
                      {isActive ? (
                        <span className="shrink-0 rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
                          aktiv
                        </span>
                      ) : null}
                    </div>
                  </button>
                  {session.use_mode === "study" && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8 shrink-0 rounded-full"
                      onClick={() => exportStudyBundle(session)}
                      disabled={exportingId !== null}
                      aria-label={`Studien-Bundle für ${sessionLabel(session)} exportieren`}
                      title="Studien-Bundle (ZIP) exportieren bzw. erneut erzeugen"
                    >
                      <Download
                        className={cn(
                          "h-3.5 w-3.5",
                          exportingId === session.id && "animate-pulse text-primary",
                        )}
                      />
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 shrink-0 rounded-full"
                    onClick={() => copySessionDebugDump(session)}
                    disabled={copyingId !== null}
                    aria-label={`Debug-Verlauf für ${sessionLabel(session)} kopieren`}
                    title="Kompletten Debug-Verlauf kopieren"
                  >
                    {copiedId === session.id ? (
                      <Check className="h-3.5 w-3.5 text-success" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                  </Button>
                </div>
              );
            })}
          </div>
        </ScrollArea>
        {copyError ? (
          <div className="rounded-xl border border-destructive/30 bg-destructive/10 px-3.5 py-2.5 text-xs text-destructive">
            {copyError}
          </div>
        ) : null}
        {exportResult ? (
          <div className="flex items-start gap-2.5 rounded-xl bg-success-soft px-3.5 py-2.5 text-xs text-success-deep">
            <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <div className="min-w-0">
              <div className="font-medium">Bundle exportiert</div>
              <div className="mt-0.5 break-all">{exportResult.bundle_path}</div>
              <div className="mt-0.5 tabular-nums">
                {(exportResult.bytes / 1024).toFixed(1)} kB,{" "}
                {exportResult.files_included.length} Dateien
              </div>
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

async function writeClipboard(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch (_err) {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "true");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    textarea.style.top = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    if (!ok) throw new Error("Clipboard API nicht verfügbar.");
  }
}

function sessionLabel(session: Session): string {
  const title = session.title?.trim();
  if (title && title !== "Neue Sitzung") return title;
  return `Sitzung ${shortId(session.id)}`;
}

function shortId(id: string): string {
  return id.slice(0, 6);
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
