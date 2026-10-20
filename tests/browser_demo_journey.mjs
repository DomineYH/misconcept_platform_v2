// D4-02 (#204) A: six-step journey on the real app (§24, §24.1, §23); runs once the G4 integrated SHA exists.
const PENDING = [
  'A: teacher start -> scenario -> two completed turns + coaching -> end/analysis/history -> admin record + CSV -> teacher delete, session delete, reset; data-demo-step and TourFacts agree at every step',
  'A: reset gives a new generation; the previous URL/tab gets 409 DEMO_STALE_GENERATION',
  'A CSV: downloaded file parsed: 27 admin + 5 suffix columns in order, 6 rows, visitor_generated, demo_external_ai_called=false, filename demo_d0-02-v1_admin_<id>.csv',
  'A CSV: teacher identity columns empty after teacher delete; deleted session absent from every export, URL, modal and API (§22)'
];

export default async function check() {
  for (const name of PENDING) console.log(`PENDING ${name}: needs G4 / #202`);
  return {pageErrors: []};
}
