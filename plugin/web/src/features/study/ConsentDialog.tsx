/**
 * ConsentDialog (Studienartefakt-Spec §2.2).
 *
 * Modal that blocks the UI until the participant has ticked every
 * checkbox and pressed "Bestätigen". Loads the Markdown text + its
 * SHA-256 from the backend; the hash round-trips on submission so the
 * server can verify the wording the participant saw matches the
 * wording on disk.
 *
 * Hard-blocking per spec: Esc and outside-click do not close the
 * dialog. Only a successful submit closes it (or programmatic close
 * via the store, e.g. when the study session is torn down).
 */

import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useChatStore } from "@/store/chatStore";
import type { ConsentTextResponse } from "@/lib/types";

interface Props {
  open: boolean;
}

export function ConsentDialog({ open }: Props) {
  const activeStudySession = useChatStore((s) => s.activeStudySession);
  const setActiveStudySession = useChatStore((s) => s.setActiveStudySession);
  const setActiveConsent = useChatStore((s) => s.setActiveConsent);
  const setConsentDialogOpen = useChatStore((s) => s.setConsentDialogOpen);

  const [bundle, setBundle] = useState<ConsentTextResponse | null>(null);
  const [checked, setChecked] = useState<boolean[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [aborting, setAborting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch the consent text fresh every time the dialog is opened so
  // that on-disk edits to consent_text.md show up without a reload.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setError(null);
    setBundle(null);
    setChecked([]);
    (async () => {
      try {
        const fetched = await api.getConsentText();
        if (cancelled) return;
        setBundle(fetched);
        setChecked(fetched.checkbox_labels.map(() => false));
      } catch (err) {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setError(`Einwilligungstext konnte nicht geladen werden: ${msg}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  const allChecked = checked.length > 0 && checked.every(Boolean);
  const canSubmit =
    allChecked &&
    !submitting &&
    !aborting &&
    bundle !== null &&
    activeStudySession !== null;

  const submit = async () => {
    if (!canSubmit || !bundle || !activeStudySession || aborting) return;
    setSubmitting(true);
    setError(null);
    try {
      const checkboxesById = Object.fromEntries(
        bundle.checkbox_labels.map((_label, i) => [
          `cb${i + 1}`,
          checked[i] ?? false,
        ]),
      );
      const consent = await api.submitConsent(activeStudySession.id, {
        study_session_id: activeStudySession.id,
        text_hash: bundle.text_hash,
        checkboxes: checkboxesById,
      });
      setActiveConsent(consent);
      setConsentDialogOpen(false);
    } catch (err) {
      console.error("Consent submit failed", err);
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Bestätigung fehlgeschlagen: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  };

  const abortStudyRun = async () => {
    if (!activeStudySession || submitting || aborting) return;
    setAborting(true);
    setError(null);
    try {
      await api.abortStudySession(activeStudySession.id, {
        stage: "consent",
        reason: "researcher_cancelled_before_consent",
      });
      setActiveStudySession(null);
      setActiveConsent(null);
      setConsentDialogOpen(false);
    } catch (err) {
      console.error("Study abort failed", err);
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Abbruch fehlgeschlagen: ${msg}`);
    } finally {
      setAborting(false);
    }
  };

  // Hard block per §2.2: Esc / outside-click must not close the
  // dialog. The shadcn Dialog wires both to onOpenChange; we swallow
  // every close attempt and only close from inside submit() on success.
  const handleOpenChange = (next: boolean) => {
    if (next) return; // opening is fine
    // ignore close attempts
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        hideClose
        className="flex max-h-[90vh] max-w-2xl flex-col gap-0 overflow-hidden p-0"
        // Block the default close behaviour (X button, Esc, overlay
        // click) at the radix level so the dialog stays modal.
        onEscapeKeyDown={(e) => e.preventDefault()}
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
      >
        <DialogHeader className="shrink-0 border-b border-border/60 px-6 pt-6 pb-4 text-left">
          <div className="flex items-start gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10">
              <ShieldCheck className="h-4 w-4 text-primary" />
            </span>
            <div className="min-w-0 space-y-1.5">
              <DialogTitle>Einwilligung zur Studienteilnahme</DialogTitle>
              <DialogDescription className="leading-relaxed">
                Bitte lesen Sie den Text und bestätigen Sie jeden Punkt, um die
                Sitzung zu beginnen.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {bundle === null && !error && (
            <p className="text-sm text-muted-foreground">Lade Einwilligungstext …</p>
          )}
          {bundle !== null && (
            <div className="rounded-xl bg-muted/40 p-4 text-sm leading-relaxed">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  h1: ({ children }) => (
                    <h3 className="mt-5 mb-1.5 text-sm font-semibold text-foreground first:mt-0">
                      {children}
                    </h3>
                  ),
                  h2: ({ children }) => (
                    <h3 className="mt-5 mb-1.5 text-sm font-semibold text-foreground first:mt-0">
                      {children}
                    </h3>
                  ),
                  p: ({ children }) => (
                    <p className="mb-2.5 text-sm leading-relaxed text-muted-foreground last:mb-0">
                      {children}
                    </p>
                  ),
                  ul: ({ children }) => (
                    <ul className="mb-2.5 list-disc space-y-1 pl-5 text-sm text-muted-foreground marker:text-muted-foreground/50">
                      {children}
                    </ul>
                  ),
                  ol: ({ children }) => (
                    <ol className="mb-2.5 list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
                      {children}
                    </ol>
                  ),
                  li: ({ children }) => (
                    <li className="leading-relaxed">{children}</li>
                  ),
                  strong: ({ children }) => (
                    <strong className="font-semibold text-foreground">
                      {children}
                    </strong>
                  ),
                  em: ({ children }) => (
                    <em className="italic text-muted-foreground/90">{children}</em>
                  ),
                  hr: () => <hr className="my-4 border-border/60" />,
                  a: ({ href, children }) => (
                    <a
                      href={href}
                      target="_blank"
                      rel="noreferrer"
                      className="font-medium text-primary underline underline-offset-2"
                    >
                      {children}
                    </a>
                  ),
                }}
              >
                {bundle.text}
              </ReactMarkdown>
            </div>
          )}
          {bundle !== null && (
            <div className="mt-4 space-y-2">
              {bundle.checkbox_labels.map((label, i) => (
                <label
                  key={i}
                  className="flex cursor-pointer items-start gap-3 rounded-xl border border-border/60 bg-card px-4 py-3 text-sm leading-relaxed transition-colors duration-200 hover:bg-muted/40"
                >
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer accent-[hsl(var(--primary))]"
                    checked={checked[i] ?? false}
                    onChange={(e) =>
                      setChecked((prev) => {
                        const next = [...prev];
                        next[i] = e.target.checked;
                        return next;
                      })
                    }
                  />
                  <span className="break-words">{label}</span>
                </label>
              ))}
            </div>
          )}
        </div>

        <div className="shrink-0 border-t border-border/60 bg-card px-6 py-4">
          {error && (
            <div className="mb-3 rounded-xl border border-destructive/30 bg-destructive/10 px-3.5 py-2.5 text-xs text-destructive">
              {error}
            </div>
          )}
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={abortStudyRun}
              disabled={submitting || aborting}
            >
              {aborting ? "Breche ab …" : "Studienlauf abbrechen"}
            </Button>
            <Button onClick={submit} disabled={!canSubmit}>
              {submitting ? "Bestätige …" : "Bestätigen und Sitzung starten"}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}
