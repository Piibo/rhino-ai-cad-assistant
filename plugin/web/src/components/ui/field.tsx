import type { ReactNode } from "react";
import { Label } from "@/components/ui/label";

/**
 * Labelled form field with an optional description line. Shared by the
 * settings and study dialogs to keep their form layout consistent.
 */
export function Field({
  label,
  description,
  children,
}: {
  label: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
      {description && (
        <p className="text-xs text-muted-foreground">{description}</p>
      )}
    </div>
  );
}
