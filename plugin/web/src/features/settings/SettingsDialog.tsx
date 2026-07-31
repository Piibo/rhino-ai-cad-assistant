import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Field } from "@/components/ui/field";
import { api, type BackendVersion } from "@/lib/api";
import { useChatStore } from "@/store/chatStore";
import { useCondition } from "@/hooks/useCondition";
import type { Settings } from "@/lib/types";

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}

// Choices the UI surfaces: Sonnet (study default) + Opus tiers for the study /
// real work, plus "OpenAI-kompatibel / Lokal" which routes via litellm to any
// OpenAI-compatible endpoint — cloud (OpenAI / OpenRouter) or local (LM Studio
// / Ollama). Other Anthropic IDs (Opus 4.6, …) are still accepted by the
// backend and appear as "Eigener Wert: …" if config.json was edited manually.
// Keep in sync with backend/config.py _KNOWN_MODELS.
const MODEL_CHOICES = [
  {
    id: "claude-sonnet-5",
    label: "Claude Sonnet 5 — Studie/Default (1M ctx, $3 / $15, Einf. $2 / $10)",
  },
  {
    id: "claude-sonnet-4-6",
    label: "Claude Sonnet 4.6 — Vorgänger (1M ctx, $3 / $15)",
  },
  {
    id: "claude-opus-4-8",
    label: "Claude Opus 4.8 — stärkstes Modell (1M ctx, $5 / $25)",
  },
  {
    id: "claude-opus-4-7",
    label: "Claude Opus 4.7 (1M ctx, $5 / $25)",
  },
  {
    id: "claude-haiku-4-5",
    label: "Claude Haiku 4.5 — fürs Entwickeln, ~5¢/Session (200K ctx, $1 / $5)",
  },
  {
    id: "local",
    label: "OpenAI-kompatibel / Lokal (OpenAI, OpenRouter, LM Studio, Ollama)",
  },
];

export function SettingsDialog({ open, onOpenChange }: Props) {
  const settings = useChatStore((s) => s.settings);
  const setSettings = useChatStore((s) => s.setSettings);
  const activeCondition = useCondition();
  const [draft, setDraft] = useState<Settings | null>(settings);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [backendVer, setBackendVer] = useState<BackendVersion | null>(null);

  useEffect(() => {
    if (open) {
      setDraft(settings);
      setSaveError(null);
    }
  }, [open, settings]);

  useEffect(() => {
    if (open) {
      api.getVersion().then(setBackendVer).catch(() => setBackendVer(null));
    }
  }, [open]);

  if (!draft) return null;

  const update = <K extends keyof Settings>(key: K, value: Settings[K]) =>
    setDraft({ ...draft, [key]: value });

  // When the user starts typing an API key while backend_mode is still "mcp",
  // flip the mode automatically — entering a key implies they want API mode.
  // Saves the common footgun: key entered, mode forgotten, chat hangs with
  // "Backend-Modus ist 'mcp'" error.
  const updateApiKey = (value: string) => {
    setDraft((prev) =>
      prev === null
        ? prev
        : {
            ...prev,
            api_key: value,
            backend_mode:
              value.length > 0 && prev.backend_mode !== "api"
                ? "api"
                : prev.backend_mode,
          },
    );
  };

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await api.updateSettings(draft);
      setSettings(updated);
      onOpenChange(false);
    } catch (err) {
      console.error("Saving settings failed", err);
      const msg = err instanceof Error ? err.message : String(err);
      setSaveError(`Speichern fehlgeschlagen: ${msg}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90vh] max-w-xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 border-b border-border/60 px-6 pt-6 pb-4">
          <DialogTitle>Einstellungen</DialogTitle>
          <DialogDescription className="leading-relaxed">
            Plugin-Konfiguration. Alle CAD-Funktionen sind in beiden Modi
            verfügbar; der Studienmodus ergänzt die Evaluations-Ebene.
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-6 py-4">
        <Tabs defaultValue="general" className="mt-1">
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="general">Allgemein</TabsTrigger>
            <TabsTrigger value="study">Nutzerstudie</TabsTrigger>
          </TabsList>

          <TabsContent value="general" className="space-y-5 py-4">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              KI-Anbindung
            </h3>
            <Field
              label="Backend-Modus"
              description={
                draft.use_mode === "study"
                  ? "Im Studienmodus gesperrt — die Studie muss im API-Modus laufen (Studienartefakt-Spec §3.5)."
                  : undefined
              }
            >
              <select
                className="h-10 w-full cursor-pointer rounded-xl border border-input bg-card px-3 text-sm transition-colors duration-200 hover:border-muted-foreground/40 focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
                value={draft.backend_mode}
                onChange={(e) =>
                  update("backend_mode", e.target.value as "mcp" | "api")
                }
                disabled={draft.use_mode === "study"}
              >
                <option value="mcp">MCP (via Claude Code / Desktop)</option>
                <option value="api">Direkter API-Aufruf (Anthropic / OpenAI)</option>
              </select>
            </Field>

            <Field
              label="API-Key"
              description={
                draft.model === "local"
                  ? "Bei lokalem Modell nicht erforderlich."
                  : "Nur für Anthropic-Modelle (Cloud). Wird lokal in config.json gespeichert. Wenn du einen Key einträgst, wird der Modus automatisch auf 'Direkter API-Aufruf' gesetzt. Für einen OpenAI-kompatiblen Endpoint stattdessen Modell 'OpenAI-kompatibel / Lokal' wählen und den Key dort eintragen."
              }
            >
              <Input
                type="password"
                value={draft.api_key}
                onChange={(e) => updateApiKey(e.target.value)}
                placeholder="sk-ant-…"
                disabled={draft.model === "local"}
              />
            </Field>

            <Field
              label="Modell"
              description={
                draft.use_mode === "study"
                  ? "Im Studienmodus gesperrt — das Modell wird vor Studienstart fixiert (Studienartefakt-Spec §3.4) und gilt für alle Teilnehmenden."
                  : undefined
              }
            >
              <select
                className="h-10 w-full cursor-pointer rounded-xl border border-input bg-card px-3 text-sm transition-colors duration-200 hover:border-muted-foreground/40 focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
                value={
                  MODEL_CHOICES.some((m) => m.id === draft.model)
                    ? draft.model
                    : "__custom"
                }
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === "__custom") return;
                  // Picking "local" implies API mode — otherwise chat would
                  // sit in the MCP bridge path which only routes to Claude.
                  setDraft((prev) =>
                    prev === null
                      ? prev
                      : {
                          ...prev,
                          model: v,
                          backend_mode:
                            v === "local" ? "api" : prev.backend_mode,
                        },
                  );
                }}
                disabled={
                  draft.backend_mode !== "api" || draft.use_mode === "study"
                }
              >
                {MODEL_CHOICES.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
                {!MODEL_CHOICES.some((m) => m.id === draft.model) && (
                  <option value="__custom">
                    {`Eigener Wert: ${draft.model}`}
                  </option>
                )}
              </select>
            </Field>

            {draft.model === "local" && (
              <div className="space-y-4 rounded-xl bg-muted/40 p-4">
                <p className="text-xs leading-relaxed text-muted-foreground">
                  OpenAI-kompatibler Endpoint (Cloud oder lokal), via litellm.
                  Beispiel OpenAI: URL https://api.openai.com/v1, Modell-ID z.B.
                  gpt-5.4, Key sk-…. OpenRouter analog mit Key. LM Studio /
                  Ollama lokal ohne Key.
                </p>
                <Field
                  label="Endpoint URL"
                  description="Cloud: https://api.openai.com/v1 — LM Studio: http://localhost:1234/v1 — Ollama: http://localhost:11434/v1"
                >
                  <Input
                    value={draft.local_base_url}
                    onChange={(e) => update("local_base_url", e.target.value)}
                    placeholder="http://localhost:1234/v1"
                  />
                </Field>
                <Field
                  label="Modell-ID"
                  description="Exakte Modell-ID des Anbieters, z.B. gpt-5.4 (OpenAI) oder qwen2.5-coder-14b-instruct (LM Studio)"
                >
                  <Input
                    value={draft.local_model}
                    onChange={(e) => update("local_model", e.target.value)}
                    placeholder="gpt-5.4"
                  />
                </Field>
                <Field
                  label="API-Key (nur Cloud)"
                  description="Für OpenAI / OpenRouter nötig. Bei LM Studio / Ollama leer lassen. Wird lokal in config.json gespeichert."
                >
                  <Input
                    type="password"
                    value={draft.local_api_key}
                    onChange={(e) => update("local_api_key", e.target.value)}
                    placeholder="sk-…"
                  />
                </Field>
              </div>
            )}

            <h3 className="pt-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Grenzen &amp; Sitzung
            </h3>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Max Tokens">
                <Input
                  type="number"
                  value={draft.max_tokens}
                  onChange={(e) =>
                    update("max_tokens", Number(e.target.value) || 0)
                  }
                />
              </Field>
              <Field label="Max Varianten">
                <Input
                  type="number"
                  value={draft.max_variants}
                  onChange={(e) =>
                    update("max_variants", Number(e.target.value) || 0)
                  }
                />
              </Field>
            </div>

            <Field
              label="Max. Werkzeug-Iterationen pro Nachricht"
              description="Begrenzt die Tool-Use-Runden (Model → Tools → Model → …) pro Nutzer-Nachricht. Höher = die KI darf längere Aufgaben autonom durchziehen (z.B. GH-Bauten), aber kostet pro Run mehr Token. 25 ist ein guter Default; 40+ wenn du der KI viel Autonomie geben willst."
            >
              <Input
                type="number"
                min={1}
                max={100}
                value={draft.max_iterations}
                onChange={(e) =>
                  update("max_iterations", Number(e.target.value) || 0)
                }
              />
            </Field>

            <Field
              label="Studienbedingung (für die nächste Session)"
              description={
                draft.use_mode === "study"
                  ? "Im Studienmodus wird die Bedingung pro Session vom Pre-Session-Dialog gesetzt — dieser Default ist dann gesperrt."
                  : "Beide Bedingungen nutzen denselben CAD-Kern (gleiche Modellierkompetenz). Werkzeug = dieser CAD-Kern + alle Interaktionswerkzeuge und UI-Slots (Sketch, Pick, Slider, Varianten, Locks, Dialogkarten). Basis = derselbe CAD-Kern, aber nur Chat-Eingabe und Bildanhang, keine sichtbaren Interaktionswerkzeuge. Wird beim Anlegen einer Session übernommen und bleibt für diese Session unveränderlich."
              }
            >
              <select
                className="h-10 w-full cursor-pointer rounded-xl border border-input bg-card px-3 text-sm transition-colors duration-200 hover:border-muted-foreground/40 focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
                value={draft.default_condition}
                onChange={(e) =>
                  update(
                    "default_condition",
                    e.target.value as "basis" | "werkzeug",
                  )
                }
                disabled={draft.use_mode === "study"}
              >
                <option value="werkzeug">Werkzeug (CAD-Kern + Interaktionswerkzeuge)</option>
                <option value="basis">Basis (CAD-Kern, nur Chat/Bild)</option>
              </select>
              {draft.use_mode !== "study" &&
                draft.default_condition !== activeCondition && (
                  <p className="mt-2 rounded-lg bg-primary/10 px-3 py-2 text-xs leading-relaxed text-primary">
                    Die aktuelle Sitzung bleibt in „
                    {activeCondition === "werkzeug" ? "Werkzeug" : "Basis"}
                    " — die Änderung greift erst beim Anlegen einer neuen
                    Sitzung (+-Button in der Kopfzeile).
                  </p>
                )}
            </Field>
          </TabsContent>

          <TabsContent value="study" className="space-y-5 py-4">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Studienmodus
            </h3>
            <div className="flex items-center justify-between gap-3 rounded-xl bg-muted/40 px-4 py-3">
              <div>
                <Label htmlFor="study-toggle">Studienmodus aktivieren</Label>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  Schaltet Consent, Studien-Session-Flow, Recording-Badge,
                  Surveys und Export hinzu.
                </p>
              </div>
              <Switch
                id="study-toggle"
                checked={draft.use_mode === "study"}
                onCheckedChange={(v) => update("use_mode", v ? "study" : "normal")}
              />
            </div>

            <div className="rounded-xl bg-muted/40 px-4 py-3 text-xs leading-relaxed text-muted-foreground">
              <p className="font-semibold text-foreground">
                Ablauf im Studienmodus
              </p>
              <ol className="mt-1.5 list-inside list-decimal space-y-1">
                <li>Studienmodus aktivieren und speichern.</li>
                <li>
                  Danach oben auf + klicken: Dort werden Teilnehmer-Kürzel,
                  Bedingung, Aufgabenvariante, Setting und Pilot-Flag gesetzt.
                </li>
                <li>
                  Anschließend erscheint der Einwilligungsdialog. Erst danach
                  kann die Person chatten.
                </li>
                <li>
                  Am Ende ueber den Stop-Button die Agency-Abfrage starten und
                  das Export-Bundle erzeugen.
                </li>
              </ol>
            </div>

            <h3 className="pt-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Aufzeichnung &amp; Export
            </h3>
            <Field label="Snapshot-Intervall (Sekunden)">
              <Input
                type="number"
                value={draft.snapshot_interval_seconds}
                onChange={(e) =>
                  update(
                    "snapshot_interval_seconds",
                    Number(e.target.value) || 0,
                  )
                }
                disabled={draft.use_mode !== "study"}
              />
            </Field>

            <Field
              label="Export-Verzeichnis"
              description="Leer = ~/Documents/masterarbeit-studie/exports (Standard). Pfad-Expansion mit ~ erfolgt im Backend."
            >
              <Input
                value={draft.export_dir}
                onChange={(e) => update("export_dir", e.target.value)}
                placeholder="~/Documents/masterarbeit-studie/exports"
              />
            </Field>
          </TabsContent>
        </Tabs>
        </div>

        <div className="shrink-0 border-t border-border/60 bg-card px-6 py-4">
          <p className="mb-3 text-[10px] leading-relaxed text-muted-foreground/70">
            Build <span className="font-mono">{__BUILD_SHA__}</span>
            {backendVer && (
              <>
                {" · "}Backend{" "}
                <span className="font-mono">{backendVer.backend_sha}</span>
                {backendVer.backend_sha !== "unknown" &&
                  __BUILD_SHA__ !== "unknown" &&
                  backendVer.backend_sha !== __BUILD_SHA__ && (
                    <span className="text-brand-orange-deep">
                      {" — ≠ , evtl. neu bauen + Rhino neu starten"}
                    </span>
                  )}
              </>
            )}
          </p>
          {saveError && (
            <div className="mb-3 rounded-xl border border-destructive/30 bg-destructive/10 px-3.5 py-2.5 text-xs text-destructive">
              {saveError}
            </div>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Abbrechen
            </Button>
            <Button onClick={save} disabled={saving}>
              {saving ? "Speichern…" : "Speichern"}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}
