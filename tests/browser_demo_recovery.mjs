// D4-02 (#204) I: recovery, language and accessibility on the real app (§25, §24); runs once the G4 integrated SHA exists.
const PENDING = [
  'I: refresh, back/forward and BFCache re-sync state without re-issuing a POST',
  'I: double clicks leave session and turn counts unchanged',
  'I: tour close/skip/resume change guidance only',
  'I: role switch/reset in a second tab shows the stale notice; zero old-generation writes',
  'I: ko/en toggle keeps the draft and the step',
  'I: 390x844, keyboard-only and 200% zoom keep input/confirm/end controls unobscured',
  'I: after expiry (helper test clock) only a new explicit start re-enters'
];

export default async function check() {
  for (const name of PENDING) console.log(`PENDING ${name}: needs G4 / #202`);
  return {pageErrors: []};
}
