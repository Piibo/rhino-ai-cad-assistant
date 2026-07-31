/**
 * Minimal recording indicator for an active, consented study run.
 *
 * The full text stays in the native hover tooltip so the compact Rhino
 * panel header does not get squeezed by a long badge.
 */

interface Props {
  active: boolean;
}

export function RecordingBadge({ active }: Props) {
  if (!active) return null;

  return (
    <span
      className="inline-flex shrink-0 items-center rounded-full bg-brand-orange-soft px-2.5 py-1.5"
      aria-label="Studienaufzeichnung läuft"
      title="Studienaufzeichnung läuft"
    >
      {/* In-place Opacity-Puls statt animate-ping — s. StudyAbortMenu: der
          scale(2)-Ring des Pings wurde in der Rhino-WebView angeschnitten. */}
      <span className="inline-flex h-2.5 w-2.5 animate-pulse rounded-full bg-brand-orange" />
    </span>
  );
}
