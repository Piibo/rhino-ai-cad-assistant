import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  // clearViewport === true  -> hide current geometry, then start fresh
  // clearViewport === false -> keep geometry, just open a new session
  onConfirm: (clearViewport: boolean) => Promise<void> | void;
}

// Confirmation before spawning a fresh session. Offers to clear the
// viewport (hide — never delete — the current geometry onto a hidden,
// timestamped "Sitzung <…>" layer) so the user can start from an empty
// scene. Recoverable via Rhino's Layer-Panel or Ctrl+Z. Deliberately a
// confirm step so a misclick on "+" can't wipe the visible model.
export function NewSessionDialog({ open, onOpenChange, onConfirm }: Props) {
  const [pending, setPending] = useState<"clear" | "keep" | null>(null);

  const handle = async (clearViewport: boolean) => {
    if (pending) return;
    setPending(clearViewport ? "clear" : "keep");
    try {
      await onConfirm(clearViewport);
    } finally {
      setPending(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !pending && onOpenChange(v)}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Neue Sitzung starten</DialogTitle>
          <DialogDescription className="leading-relaxed">
            Soll die aktuelle Geometrie ausgeblendet werden, damit du mit
            leerem Viewport startest? Nichts wird gelöscht — die bisherige
            Geometrie wandert auf einen versteckten Layer und laesst sich im
            Rhino-Layer-Panel (oder mit Strg+Z) jederzeit wieder einblenden.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2 pt-2 sm:gap-2">
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={pending !== null}
          >
            Abbrechen
          </Button>
          <Button
            variant="outline"
            onClick={() => handle(false)}
            disabled={pending !== null}
          >
            {pending === "keep" ? "Starte…" : "Nur neue Sitzung"}
          </Button>
          <Button
            variant="default"
            onClick={() => handle(true)}
            disabled={pending !== null}
          >
            {pending === "clear" ? "Blende aus…" : "Ausblenden & neu starten"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
