// Fixture-only transport; never served by application routes.
import {mountLessonHelp} from '/static/js/lesson-help.js';

const lesson = JSON.parse(document.getElementById('lesson-fixture-data').textContent);
const controls = mountLessonHelp(document.getElementById('lesson-controls'), lesson.help, async trigger => {
  const response = await fetch(`/sessions/${lesson.session_id}/turns/${lesson.completed_turn_id}/mentor/stream`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({request_id:crypto.randomUUID(), trigger})
  });
  if (!response.ok) throw new Error('Fixture request failed');
  return response.json();
});
if (lesson.auto_eligible && lesson.help.status === 'ready') controls?.request('auto');
