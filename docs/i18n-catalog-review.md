# 표시 언어 카탈로그 검토 (#137)

**상태: pending user review.** 이 표는 검토 자료이며 승인 기록이 아니다. 코디네이터가 PR에서 사용자 검토를 요청하고 피드백과 반영 결과를 기록한다. #133의 사용자 검토 완료 조건은 그때까지 미완료다.

`src/i18n/catalog.json`의 모든 항목을 영역별로 나열했다. 키는 개발 참조용이며 화면에 표시하지 않는다. `{name}` 등의 보간 인자는 원문 콘텐츠를 삽입하는 자리다.

총 1210개 키. `tests/test_i18n_catalog.py`에서 KO/EN 값, 보간 인자, 코드 참조 및 미사용 키를 검사한다. 알 수 없는 키와 영어 누락의 안전한 fallback은 서버·브라우저 언어 테스트로 확인한다.

## 번역하지 않는 값과 예외

- 사용자/AI 원문: 시나리오 제목·문제·지시·분류 기준, 이름·별명·그룹·소개·연락처, 메시지·멘토 코칭·사후 분석·비교 출력과 저장 이력. 표시 언어는 AI 생성 언어·실행 의미를 변경하지 않는다.
- 승인된 과거 변환 증거: `conversion_provenance.field/source/target`에 저장된 한국어 문장·접두사를 그대로 보존한다. 현재 UI의 주변 레이블은 번역하며 저장 JSON을 다시 쓰지 않는다.
- 저장된 검토 안내는 원문 문장을 역번역하지 않고 명시적 `code/path`로 표시한다. `title/invalid_source`처럼 모호한 조합은 필드별 일반 안내를 쓴다.
- 브라우저/프레임워크 기본 UI: 네이티브 확인 대화상자 버튼·파일 선택·입력 유효성 및 FastAPI/Pydantic의 기본 검증 문구. 앱이 제공하는 확인 본문·안내·오류는 번역 대상이다.
- 언어 자체 이름 `한국어`/`English`, 기호·아바타 이니셜, 국제 약어·단위 `ID`/`CSV`/`SHA-256`/`N/A`/`ms`/`USD`, 제공자·모델 식별자, API 옵션 이름·enum·코드·경로·시간·해시·CSV 데이터 헤더는 계약 값으로 유지한다. 그 값을 설명하는 앱 레이블은 번역한다.
- 주석·로그·AI 프롬프트·합성 검증 입력은 화면 문구가 아니다. 기존 프롬프트나 저장 데이터의 언어를 바꾸지 않는다.

## common (15)

| area | key | ko | en |
| --- | --- | --- | --- |
| common | `common.check_input` | 입력을 확인하세요. | Check your input. |
| common | `common.product` | 오개념 교정 대화 시뮬레이터 | Misconception Dialogue Simulator |
| common | `common.skip` | 본문으로 건너뛰기 | Skip to main content |
| common | `common.navigation` | 주 탐색 | Main navigation |
| common | `common.menu` | 메뉴 | Menu |
| common | `common.scenarios` | 시나리오 | Scenarios |
| common | `common.admin` | 관리자 | Administration |
| common | `common.account` | 계정 | Account |
| common | `common.logout` | 로그아웃 | Log out |
| common | `common.footer` | 교육 연구 도구 | Educational research tool |
| common | `common.close` | 닫기 | Close |
| common | `common.language` | 표시 언어 | Display language |
| common | `common.guard_input` | 저장하거나 입력을 비운 뒤 전환하세요 | Save or clear your input before switching languages. |
| common | `common.guard_busy` | 작업이 끝난 뒤 전환하세요 | Wait until the task finishes before switching languages. |
| common | `common.cookie_failed` | 언어 설정을 저장할 수 없습니다. 쿠키를 허용한 뒤 다시 시도하세요. | Could not save your language. Allow cookies and try again. |

## login (13)

| area | key | ko | en |
| --- | --- | --- | --- |
| login | `login.title` | 로그인 | Log in |
| login | `login.subtitle` | 교사 연수 플랫폼 | Teacher training platform |
| login | `login.username` | 사용자 ID | User ID |
| login | `login.password` | 비밀번호 | Password |
| login | `login.required` | (필수) | (required) |
| login | `login.username_placeholder` | 사용자 ID 입력 | Enter user ID |
| login | `login.password_placeholder` | 비밀번호 입력 | Enter password |
| login | `login.username_format` | 영문, 숫자, 언더스코어만 사용 가능 | Use letters, numbers and underscores only |
| login | `login.username_help` | 3-50자, 영문/숫자/언더스코어만 사용 | 3–50 characters: letters, numbers and underscores only |
| login | `login.note` | 참고: | Note: |
| login | `login.help` | 관리자가 발급한 ID와 비밀번호로 로그인하세요. 계정이 없는 경우 관리자에게 문의하세요. | Log in with the ID and password issued by your administrator. Contact your administrator if you do not have an account. |
| login | `login.invalid_credentials` | 사용자 ID 또는 비밀번호가 올바르지 않습니다. | The user ID or password is incorrect. |
| login | `login.invalid_username` | 사용자 ID는 영문, 숫자, 언더스코어만 사용 가능합니다. | User IDs may contain only letters, numbers and underscores. |

## about (7)

| area | key | ko | en |
| --- | --- | --- | --- |
| about | `about.title` | about | About |
| about | `about.heading` | About | About |
| about | `about.phone` | 전화 | Phone |
| about | `about.email` | 이메일 | Email |
| about | `about.empty` | 등록된 정보가 없습니다. | No information has been added. |
| about | `about.loading` | 소개를 불러오는 중... | Loading About… |
| about | `about.load_error` | 소개를 불러올 수 없습니다. About을 다시 눌러 주세요. | Could not load About. Select About to try again. |

## teacher (330)

| area | key | ko | en |
| --- | --- | --- | --- |
| teacher | `teacher.scenarios_title` | 시나리오 - 오개념 교정 대화 시뮬레이터 | Scenarios - Misconception Dialogue Simulator |
| teacher | `teacher.choose_scenario` | 시나리오 선택 | Choose a scenario |
| teacher | `teacher.scenario_intro` | 공개 문제와 학생 소개를 확인하고 대화를 시작하세요. | Read the public problem and student profile, then start a conversation. |
| teacher | `teacher.problem_needed` | 문제 상황 보완 필요 | Problem statement needed |
| teacher | `teacher.public_profile` | 공개 학생 소개 | Public student profile |
| teacher | `teacher.start_chat` | 대화 시작 | Start conversation |
| teacher | `teacher.problem_unavailable` | 공개 문제 상황이 설정되지 않아 시작할 수 없습니다. 관리자에게 문의하세요. | A public problem is required to start. Contact an administrator. |
| teacher | `teacher.no_scenarios` | 시나리오가 없습니다 | No scenarios available |
| teacher | `teacher.no_assigned_scenarios` | 현재 사용할 수 있는 배정된 시나리오가 없습니다. | No assigned scenarios are currently available. |
| teacher | `teacher.check_assignment` | 시나리오 배정과 게시 상태를 관리자에게 문의하세요. | Ask an administrator about scenario assignment and publication. |
| teacher | `teacher.chat_screen` | 대화 화면 | Conversation screen |
| teacher | `teacher.situation_info` | 상황 정보 | Scenario information |
| teacher | `teacher.chat` | 대화 | Conversation |
| teacher | `teacher.panel_toggle_shortcut` | 정보 패널 접기/펼치기 (Ctrl+B) | Toggle information panel (Ctrl+B) |
| teacher | `teacher.panel_toggle` | 정보 패널 접기/펼치기 | Toggle information panel |
| teacher | `teacher.end_and_analyze` | 대화 종료 후 분석 보기 | End conversation and view analysis |
| teacher | `teacher.leave` | 나가기 | Leave |
| teacher | `teacher.usage_guide` | 챗봇 활용 방법 | Usage guide |
| teacher | `teacher.student_info` | 학생 정보 | Student information |
| teacher | `teacher.problem` | 문제 상황 | Problem statement |
| teacher | `teacher.no_student_info` | 학생 정보가 없습니다. | No student information available. |
| teacher | `teacher.problem_contact` | 문제 상황 보완 필요 — 관리자에게 문의하세요. | Problem statement needed — contact an administrator. |
| teacher | `teacher.learning_objective` | 학습목표: | Learning objective: |
| teacher | `teacher.target_grade` | 대상 학년: | Target grade: |
| teacher | `teacher.chat_ended` | 이 대화는 종료되었습니다. | This conversation has ended. |
| teacher | `teacher.go_login` | 로그인으로 이동 | Go to login |
| teacher | `teacher.mentor_help` | 멘토 도움 | Mentor help |
| teacher | `teacher.mentor_help_available` | 완료된 학생 턴에 도움을 요청할 수 있습니다. | You can request help for a completed student turn. |
| teacher | `teacher.request_mentor` | 멘토 도움 요청 | Request mentor help |
| teacher | `teacher.teacher_message` | 교사 메시지 | Teacher message |
| teacher | `teacher.message_placeholder` | 메시지를 입력하세요... | Enter a message... |
| teacher | `teacher.send_message` | 메시지 전송 | Send message |
| teacher | `teacher.send` | 전송 | Send |
| teacher | `teacher.analysis_results` | 세션 분석 결과 | Session analysis results |
| teacher | `teacher.mentor` | 멘토 | Mentor |
| teacher | `teacher.mentor_avatar` | 멘 | M |
| teacher | `teacher.student` | 학생 | Student |
| teacher | `teacher.guide_problem` | 문제 상황을 확인한 후 {student}의 오개념을 해결해 봅시다. | Read the problem and help {student} address their misconception. |
| teacher | `teacher.guide_analysis` | 대화가 끝나면 {action}를 클릭해 보세요. | When you finish, select {action}. |
| teacher | `teacher.guide_feedback_subject` | 대화가 학생의 {subject}적 사고를 이끌어내기에 적절했는지 피드백을 받아봅시다. | Get feedback on how the conversation supported the student’s thinking in {subject}. |
| teacher | `teacher.guide_feedback` | 대화가 학생의 사고를 이끌어내기에 적절했는지 피드백을 받아봅시다. | Get feedback on how the conversation supported the student’s thinking. |
| teacher | `teacher.analysis_title` | 세션 분석 | Session analysis |
| teacher | `teacher.analysis_report` | 세션 분석 리포트 | Session analysis report |
| teacher | `teacher.session_ended_at` | 세션 종료 시간: | Session ended: |
| teacher | `teacher.view_original` | 원래 대화 보기 | View original conversation |
| teacher | `teacher.feedback_degraded` | 일부 피드백 항목이 생성되지 않았어요 | Some feedback items could not be generated |
| teacher | `teacher.feedback_failed` | 분석 피드백을 생성할 수 없습니다. | Analysis feedback could not be generated. |
| teacher | `teacher.duration` | 대화 시간 | Conversation duration |
| teacher | `teacher.cannot_calculate` | 계산 불가 | Unavailable |
| teacher | `teacher.teacher_questions` | 선생님 질문 | Teacher questions |
| teacher | `teacher.student_responses` | 학생 응답 | Student responses |
| teacher | `teacher.mentor_interventions` | 멘토 개입 | Mentor interventions |
| teacher | `teacher.download_csv` | CSV 다운로드 | Download CSV |
| teacher | `teacher.back_scenarios` | 시나리오로 돌아가기 | Back to scenarios |
| teacher | `teacher.history_title` | 과거 기록 | Session history |
| teacher | `teacher.chat_history` | 대화 기록 | Conversation history |
| teacher | `teacher.native_provenance` | 수업 시작 시 확정한 설정 · 원래 대화 기록 | Settings captured at session start · original conversation |
| teacher | `teacher.no_end_record` | 종료 기록 없음 | No end time recorded |
| teacher | `teacher.started` | 시작: | Started: |
| teacher | `teacher.ended` | 종료: | Ended: |
| teacher | `teacher.view_existing_analysis` | 기존 분석 보기 | View saved analysis |
| teacher | `teacher.choose_new_practice` | 새 연습 선택 | Choose a new practice |
| teacher | `teacher.original_conversation` | 원래 대화 | Original conversation |
| teacher | `teacher.analysis_details` | 상세 분석 | Detailed analysis |
| teacher | `teacher.question_distribution` | 질문 유형 분포 | Question type distribution |
| teacher | `teacher.classification_off` | 분류 미사용 | Classification disabled |
| teacher | `teacher.analysis_tabs` | 상세 분석 탭 | Analysis detail tabs |
| teacher | `teacher.dialogue_coaching` | 대화코칭 | Dialogue coaching |
| teacher | `teacher.strengths` | 우수한 점 | Strengths |
| teacher | `teacher.improvements` | 개선할 점 | Improvements |
| teacher | `teacher.teacher` | 선생님 | Teacher |
| teacher | `teacher.good_dialogue` | 잘 한 대화 | Effective dialogue |
| teacher | `teacher.missed_moment` | 놓친 순간 | Missed opportunity |
| teacher | `teacher.no_messages` | 대화 내역이 없습니다. | No conversation messages. |
| teacher | `teacher.no_strengths` | 우수한 점이 없습니다. | No strengths identified. |
| teacher | `teacher.no_strong_questions` | 우수한 질문이 없습니다. | No strong questions identified. |
| teacher | `teacher.alternative_question_label` | 대안 질문: | Alternative question: |
| teacher | `teacher.no_improvements` | 개선할 점이 없습니다. | No improvements identified. |
| teacher | `teacher.improved_sentence_label` | 개선한 문장: | Improved sentence: |
| teacher | `teacher.improved_after_rerun` | 개선한 문장은 분석 재실행 후 표시됩니다. | Improved sentences appear after rerunning analysis. |
| teacher | `teacher.close` | 닫기 | Close |
| teacher | `teacher.question` | 질문 | Question |
| teacher | `teacher.classification` | 분류 | Classification |
| teacher | `teacher.grade_label` | 평가: | Grade: |
| teacher | `teacher.unknown` | 알 수 없음 | Unknown |
| teacher | `teacher.confidence` | 신뢰도 | Confidence |
| teacher | `teacher.summary_label` | 요약: | Summary: |
| teacher | `teacher.session_id_label` | 세션 ID: | Session ID: |
| teacher | `teacher.scenario_label` | 시나리오: | Scenario: |
| teacher | `teacher.analysis_time_label` | 분석 시간: | Analyzed: |
| teacher | `teacher.view_full_page` | 전체 화면으로 보기 | View full page |
| teacher | `teacher.scenario_list` | 시나리오 목록으로 | Scenario list |
| teacher | `teacher.total` | 총계 | Total |
| teacher | `teacher.legacy_provenance` | 과거 자료 · 재구성된 수업 설정 · 읽기 전용 | Historical data · reconstructed lesson settings · read only |
| teacher | `teacher.reconstructed_at` | 재구성 시각: | Reconstructed: |
| teacher | `teacher.not_reconstructed` | 아직 재구성되지 않음 | Not yet reconstructed |
| teacher | `teacher.not_original_settings` | 실제 시작 시점의 설정을 증명하지 않습니다. | This does not establish the settings at the original session start. |
| teacher | `teacher.unknown_settings` | 당시 설정을 확인할 수 없음: | Original settings unavailable: |
| teacher | `teacher.reconstruction_notice` | 설정 해시는 재구성 자료의 해시입니다. 기존 대화·분석 결과를 보존합니다. 재개·신규 발화·멘토 요청·재분석은 사용할 수 없습니다. | The settings hash describes reconstructed data. Existing conversations and analysis are preserved. Resume, new messages, mentor requests and reanalysis are unavailable. |
| teacher | `teacher.session_id` | 세션 ID | Session ID |
| teacher | `teacher.scenario` | 시나리오 | Scenario |
| teacher | `teacher.teacher_role` | 교사 | Teacher |
| teacher | `teacher.start_time` | 시작 시간 | Start time |
| teacher | `teacher.end_time` | 종료 시간 | End time |
| teacher | `teacher.status` | 상태 | Status |
| teacher | `teacher.deleted` | 삭제됨 | Deleted |
| teacher | `teacher.completed` | 완료 | Completed |
| teacher | `teacher.in_progress` | 진행중 | In progress |
| teacher | `teacher.post_analysis_result` | 사후 분석 결과 | Post-session analysis results |
| teacher | `teacher.turn_number` | {number}번째 턴 | Turn {number} |
| teacher | `teacher.question_count` | {count}개 질문 | {count} questions |
| teacher | `teacher.excellent_count` | 우수 {count}개 | Excellent: {count} |
| teacher | `teacher.improve_count` | 개선 {count}개 | Needs improvement: {count} |
| teacher | `teacher.analysis_detail_id` | 분석 상세 (ID: {id}) | Analysis details (ID: {id}) |
| teacher | `teacher.minutes` | 분 | min |
| teacher | `teacher.seconds` | 초 | sec |
| teacher | `teacher.items_unit` | 개 |  items |
| teacher | `teacher.times_unit` | 회 |  times |
| teacher | `teacher.message_failed` | 메시지를 전송하지 못했습니다. 다시 시도해주세요. | Failed to send message. Please try again. |
| teacher | `teacher.network_error` | 네트워크 오류입니다. 연결을 확인해주세요. | Network error. Please check your connection. |
| teacher | `teacher.analysis_preparing` | 분석 준비 중... | Preparing analysis... |
| teacher | `teacher.analyzing` | 분석 중... | Analyzing... |
| teacher | `teacher.view_analysis` | 분석 보기 | View analysis |
| teacher | `teacher.view_analysis_again` | 분석 다시 보기 | View analysis again |
| teacher | `teacher.panel_expand` | 정보 패널 펼치기 | Expand information panel |
| teacher | `teacher.panel_collapse` | 정보 패널 접기 | Collapse information panel |
| teacher | `teacher.draft_restored` | 이전 입력 메시지를 복원했습니다. | Your previous draft message was restored. |
| teacher | `teacher.auth_expired` | 로그인이 만료되었습니다. 다시 로그인해주세요. | Your login has expired. Please log in again. |
| teacher | `teacher.session_expired` | 세션이 만료되었습니다. 다시 로그인해 주세요. | Your session has expired. Please log in again. |
| teacher | `teacher.close_failed_navigation` | 세션 종료 요청에 실패했습니다. 계속 이동하시겠습니까? | Could not close the session. Continue navigating? |
| teacher | `teacher.analysis_modal_failed` | 분석 결과를 불러오지 못했습니다. 다시 불러오거나 닫을 수 있습니다. | Could not load analysis results. You can retry or close this dialog. |
| teacher | `teacher.reload_results` | 결과 다시 불러오기 | Reload results |
| teacher | `teacher.no_active_session` | 활성 세션이 없습니다 | No active session |
| teacher | `teacher.confirm_end` | 이 세션을 종료하시겠습니까? | End this session? |
| teacher | `teacher.analysis_failed_alert` | 분석에 실패했습니다 | Analysis failed |
| teacher | `teacher.confirm_scenarios` | 시나리오 목록으로 돌아가시겠습니까? | Return to the scenario list? |
| teacher | `teacher.already_ended` | 이 대화는 이미 종료되었습니다. | This conversation has already ended. |
| teacher | `teacher.start_new_session` | 이 세션은 이미 종료되었습니다. 새 세션을 시작해주세요. | This session has already ended. Please start a new session. |
| teacher | `teacher.request_failed` | 요청 처리 중 오류가 발생했습니다 | Could not process the request |
| teacher | `teacher.request_timeout` | 요청 시간이 초과되었습니다. 다시 시도해주세요. | The request timed out. Please try again. |
| teacher | `teacher.analysis_loading` | 분석 결과를 불러오는 중입니다. | Loading analysis results. |
| teacher | `teacher.generating` | 응답 생성 중... | Generating response... |
| teacher | `teacher.student_retry` | 학생 응답 다시 받기 | Retry student response |
| teacher | `teacher.end_chat` | 대화 종료 | End conversation |
| teacher | `teacher.check_status` | 상태 다시 확인 | Check status again |
| teacher | `teacher.sending` | 전송 중 | Sending |
| teacher | `teacher.student_provisional` | 학생 생성 중 · 잠정 응답 | Student generating · provisional response |
| teacher | `teacher.saved` | 저장 완료 | Saved |
| teacher | `teacher.student_failed` | 학생 응답 생성에 실패했습니다. | Could not generate the student response. |
| teacher | `teacher.student_interrupted` | 학생 응답이 중단되었습니다. | The student response was interrupted. |
| teacher | `teacher.conversation_ended` | 대화가 종료되었습니다. | The conversation has ended. |
| teacher | `teacher.student_forbidden` | 이 실행을 조회하거나 재시도할 권한이 없습니다. 응답 미완료·대화 기록에 미포함 | You cannot view or retry this run. Incomplete response · excluded from conversation history |
| teacher | `teacher.student_checking` | 상태 확인 중 · 응답 미완료·대화 기록에 미포함 | Checking status · incomplete response · excluded from conversation history |
| teacher | `teacher.student_unknown_request` | 요청 수락 여부를 확인할 수 없습니다. 같은 요청을 명시적으로 다시 전송할 수 있습니다. | Request acceptance is unknown. You may explicitly resend the same request. |
| teacher | `teacher.resend_request` | 같은 요청 다시 전송 | Resend same request |
| teacher | `teacher.student_checking_saved` | 학생 생성 중 · 저장 상태 확인 중 | Student generating · checking saved status |
| teacher | `teacher.student_unknown_saved` | 저장 상태가 아직 불명확합니다. 상태 다시 확인을 눌러주세요. | Saved status is still unknown. Select Check status again. |
| teacher | `teacher.student_lookup_failed` | 저장 상태를 확인하지 못했습니다. 상태 다시 확인을 눌러주세요. | Could not check saved status. Select Check status again. |
| teacher | `teacher.student_context_limit` | 입력이 너무 큽니다. 질문을 줄이거나 관리자에게 수업 설정 확인을 요청해주세요. 요청은 아직 수락되지 않았습니다. | The input is too large. Shorten your question or ask an administrator to check the lesson settings. The request has not been accepted. |
| teacher | `teacher.student_previous_incomplete` |  기존 응답 미완료·대화 기록에 미포함 |  Previous response incomplete · excluded from conversation history |
| teacher | `teacher.student_configuration` | 관리자에게 AI 연결과 학생 모델 검증을 요청해주세요. 요청은 아직 수락되지 않았습니다. | Ask an administrator to check the AI connection and student model verification. The request has not been accepted. |
| teacher | `teacher.student_capacity` | 호출 한도에 도달했습니다. 잠시 후 같은 요청을 다시 전송해주세요. 요청은 아직 수락되지 않았습니다. | The call limit has been reached. Resend the same request later. The request has not been accepted. |
| teacher | `teacher.mentor_timeout` | 멘토 응답 대기 시간이 초과되었습니다. 멘토를 다시 요청해주세요. | The mentor response timed out. Request the mentor again. |
| teacher | `teacher.mentor_invalid` | 멘토 응답 형식이 올바르지 않습니다. 멘토를 다시 요청하거나 관리자에게 설정 확인을 요청해주세요. | The mentor response format is invalid. Request the mentor again or ask an administrator to check the settings. |
| teacher | `teacher.mentor_refused` | 멘토 응답이 거절되었습니다. 멘토를 다시 요청하거나 관리자에게 설정 확인을 요청해주세요. | The mentor response was refused. Request the mentor again or ask an administrator to check the settings. |
| teacher | `teacher.context_limit` | 입력이 너무 큽니다. 질문을 줄이거나 관리자에게 수업 설정 확인을 요청해주세요. | The input is too large. Shorten your question or ask an administrator to check the lesson settings. |
| teacher | `teacher.mentor_failed` | 멘토 코칭 생성에 실패했습니다. | Could not generate mentor coaching. |
| teacher | `teacher.mentor_retry` | 멘토 다시 요청 | Request mentor again |
| teacher | `teacher.mentor_no_coaching` | 자동 검사 완료 · 코칭 없음 | Automatic check complete · no coaching |
| teacher | `teacher.mentor_no_intervention` | 멘토 미개입 | No mentor intervention |
| teacher | `teacher.mentor_completed` | 멘토 코칭 완료 | Mentor coaching complete |
| teacher | `teacher.mentor_session_ended` | 대화가 종료되어 멘토를 다시 요청할 수 없습니다. | The conversation has ended. You cannot request the mentor again. |
| teacher | `teacher.mentor_checking` | 멘토 저장 상태 확인 중 | Checking saved mentor status |
| teacher | `teacher.mentor_forbidden` | 이 멘토 실행을 조회할 권한이 없습니다. | You do not have permission to view this mentor run. |
| teacher | `teacher.mentor_unknown_request` | 멘토 요청 수락 여부를 확인할 수 없습니다. | Mentor request acceptance is unknown. |
| teacher | `teacher.mentor_unknown_saved` | 멘토 저장 상태가 아직 불명확합니다. 상태 다시 확인을 눌러주세요. | Saved mentor status is still unknown. Select Check status again. |
| teacher | `teacher.mentor_processing` | 멘토 처리 중 | Mentor processing |
| teacher | `teacher.mentor_busy` | 이전 코칭 처리 중 — 이후 다시 요청 가능 | Previous coaching in progress — you can request again later |
| teacher | `teacher.mentor_limit` | 최근 완료 턴의 개입 상한에 도달했습니다. | The intervention limit for the latest completed turn has been reached. |
| teacher | `teacher.mentor_interval` | 자동 검사 간격 대기 중 — 도움 버튼으로 요청할 수 있습니다. | Waiting for the automatic check interval — use the help button to request coaching. |
| teacher | `teacher.mentor_unavailable` | 현재 멘토 도움을 사용할 수 없습니다. | Mentor help is currently unavailable. |
| teacher | `teacher.mentor_obsolete` | 더 최신 학생 턴이 완료되어 이 턴은 더 이상 재요청할 수 없습니다. | A newer student turn has completed. You cannot request this turn again. |
| teacher | `teacher.mentor_configuration` | 관리자에게 AI 연결과 멘토 모델 검증을 요청해주세요. | Ask an administrator to check the AI connection and mentor model verification. |
| teacher | `teacher.mentor_capacity` | AI 호출이 많습니다. 잠시 후 멘토를 다시 요청해주세요. | There are too many AI calls. Request the mentor again later. |
| teacher | `teacher.mentor_request_failed` | 멘토 요청을 처리하지 못했습니다. | Could not process the mentor request. |
| teacher | `teacher.mentor_auth_expired` | 로그인이 만료되어 멘토를 다시 요청할 수 없습니다. | Your login has expired. You cannot request the mentor again. |
| teacher | `teacher.mentor_page_left` | 페이지 이동으로 멘토 코칭이 중단되었습니다. | Mentor coaching was interrupted by page navigation. |
| teacher | `teacher.student_incomplete` | {reason} 응답 미완료·대화 기록에 미포함 | {reason} Incomplete response · excluded from conversation history |
| teacher | `teacher.student_configuration_error` | 관리자에게 AI 연결과 학생 모델 검증을 요청해주세요. | Ask an administrator to check the AI connection and student model verification. |
| teacher | `teacher.mentor_target_turn` | {name} · 대상 턴 {turn} | {name} · target turn {turn} |
| teacher | `teacher.unknown_scenario_context` | 시나리오 제목·과목·학년 | Scenario title, subject and grade |
| teacher | `teacher.unknown_config` | 전체 수업 설정 | All lesson settings |
| teacher | `teacher.unknown_problem` | 공개 문제·학습목표 | Public problem and learning objective |
| teacher | `teacher.unknown_student` | 학생봇 소개·지시·모델·옵션 | Student bot profile, instructions, model and options |
| teacher | `teacher.unknown_mentor` | 멘토 설정 | Mentor settings |
| teacher | `teacher.unknown_analysis` | 분석 기준·지시·모델·옵션 | Analysis rubric, instructions, model and options |
| teacher | `teacher.unknown_runtime` | 문맥 턴 제한 | Context turn limit |
| teacher | `teacher.unknown_other` | 그 밖의 설정 | Other settings |
| teacher | `teacher.session_not_found` | 세션을 찾을 수 없습니다. | Session not found |
| teacher | `teacher.scenario_not_found` | 시나리오를 찾을 수 없습니다. | Scenario not found |
| teacher | `teacher.turn_not_found` | 턴을 찾을 수 없습니다. | Turn not found |
| teacher | `teacher.run_not_found` | 실행을 찾을 수 없습니다. | Run not found |
| teacher | `teacher.analysis_not_found` | 분석을 찾을 수 없습니다. | Analysis not found |
| teacher | `teacher.forbidden` | 접근 권한이 없습니다. | Forbidden |
| teacher | `teacher.end_before_analysis` | 분석을 보려면 먼저 세션을 종료해주세요. | End the session before viewing analysis. |
| teacher | `teacher.no_group` | 그룹이 배정되지 않았습니다. | No group has been assigned. |
| teacher | `teacher.scenario_forbidden` | 이 시나리오에 대한 접근 권한이 없습니다. | You do not have access to this scenario. |
| teacher | `teacher.start_failed` | 대화 세션을 시작할 수 없습니다. 잠시 후 다시 시도해주세요. | Could not start the conversation. Please try again later. |
| teacher | `teacher.reload_required` | 새로고침 후 다시 전송해주세요. | Reload the page before sending again. |
| teacher | `teacher.legacy_read_only_error` | 과거 세션은 읽기 전용입니다. 게시된 시나리오로 새 연습을 시작해주세요. | Historical sessions are read only. Start a new practice with a published scenario. |
| teacher | `teacher.shorten_question` | 질문을 짧게 수정하거나 관리자에게 AI 설정을 확인해주세요. | Shorten the question or ask an administrator to check the AI settings. |
| teacher | `teacher.check_ai_settings` | 관리자에게 AI 설정을 확인하거나 잠시 후 다시 시도해주세요. | Ask an administrator to check the AI settings or try again later. |
| teacher | `teacher.check_mentor_settings` | 관리자에게 AI 연결과 멘토 모델 검증을 요청하거나 잠시 후 다시 시도해주세요. | Ask an administrator to check the AI connection and mentor model verification, or try again later. |
| teacher | `teacher.latest_run` | 최신 실행: {status} | Latest run: {status} |
| teacher | `teacher.source_unknown` | 원문 위치 알 수 없음: {quote} | Source location unknown: {quote} |
| teacher | `teacher.source_quote` | 원문 {id}: {quote} | Source {id}: {quote} |
| teacher | `teacher.source_moved` | 메시지 {id} 원문으로 이동했습니다. | Moved to the original text of message {id}. |
| teacher | `teacher.run_started` | 실행 시작: {time} | Run started: {time} |
| teacher | `teacher.run_finished` | 실행 종료: {time} | Run finished: {time} |
| teacher | `teacher.report_preserved` | {report} 보존했습니다. 최신 실행의 결과와 구별해 확인하세요. | {report} was preserved. Review it separately from the latest run outcome. |
| teacher | `teacher.plan_range` | 대상 범위: 메시지 {ids} · {count}개 | Target coverage: messages {ids} · {count} items |
| teacher | `teacher.plan_calls` | {chunks}개 분할 + 종합 · 예상 생성 호출 {calls}회 | {chunks} chunks + synthesis · {calls} estimated generation calls |
| teacher | `teacher.plan_tokens` | 추정 입력 {input} · 추정 출력 {output} 토큰 | Estimated input {input} · estimated output {output} tokens |
| teacher | `teacher.plan_retries` | 일시 장애 재시도 상한: 호출당 {limit}회 · 최대 {attempts}회 시도 | Transient error retry limit: {limit} per call · at most {attempts} attempts |
| teacher | `teacher.chunk_messages` | 분할 {number}: 메시지 {ids} | Chunk {number}: messages {ids} |
| teacher | `teacher.merge_tokens` | 종합 입력 상한 추정 {input} · 종합 출력 추정 {output} 토큰 | Estimated synthesis input limit {input} · estimated synthesis output {output} tokens |
| teacher | `teacher.report_status` | {report}: {status} | {report}: {status} |
| teacher | `teacher.report_created` | 생성 시각: {time} | Created: {time} |
| teacher | `teacher.conversation_reviewed` | 대화 검토: {reviewed} / {total}개 메시지 | Conversation reviewed: {reviewed} / {total} messages |
| teacher | `teacher.reviewed_messages` | 검토한 메시지: {ids} | Reviewed messages: {ids} |
| teacher | `teacher.classification_denominator` | 분류 비율 분모: {classified}개 · 전체 교사 발화: {total}개 | Classification denominator: {classified} · all teacher messages: {total} |
| teacher | `teacher.coverage_count` | {label}: {count}개 | {label}: {count} items |
| teacher | `teacher.affected_messages` | 해당 메시지: {ids} | Affected messages: {ids} |
| teacher | `teacher.missing_student_count` | 학생 응답 없음: {count}개 | Missing student responses: {count} |
| teacher | `teacher.missing_student_evidence` | 해당 메시지: {ids} · 학생 이해 변화의 근거로 사용하지 않습니다. | Affected messages: {ids} · not used as evidence of changes in student understanding. |
| teacher | `teacher.chunk_status` | 분할 {status} · 메시지 {ids} | Chunk {status} · messages {ids} |
| teacher | `teacher.not_run` | 미실행 | Not run |
| teacher | `teacher.distribution_item` | {name}: {count}개 · {percentage} | {name}: {count} items · {percentage} |
| teacher | `teacher.alternative_question` | 대안 질문: {question} | Alternative question: {question} |
| teacher | `teacher.message_role` | 메시지 {id} · {role} | Message {id} · {role} |
| teacher | `teacher.message_time` | 시각: {time} | Time: {time} |
| teacher | `teacher.message_grade` | 분류: {label} · 평가: {grade} | Classification: {label} · grade: {grade} |
| teacher | `teacher.message_reason` | 판정 이유: {reason} | Reason: {reason} |
| teacher | `teacher.improved_sentence` | 개선한 문장: {sentence} | Improved sentence: {sentence} |
| teacher | `teacher.analysis_ok` | 정상 분석 | Complete analysis |
| teacher | `teacher.analysis_degraded` | 부분 분석 | Degraded analysis |
| teacher | `teacher.analysis_failed` | 분석 실패 | Analysis failed |
| teacher | `teacher.analysis_legacy` | 과거 분석 | Historical analysis |
| teacher | `teacher.analysis_running` | 분석 진행 중 | Analysis running |
| teacher | `teacher.analysis_cancelled` | 분석 취소됨 | Analysis cancelled |
| teacher | `teacher.analysis_interrupted` | 분석 중단됨 | Analysis interrupted |
| teacher | `teacher.no_dialogue` | 대화 없음 | No dialogue |
| teacher | `teacher.analysis_ready` | 분석 실행 전 | Analysis not started |
| teacher | `teacher.plan_required` | 분할 계획 확인 대기 | Awaiting chunk plan confirmation |
| teacher | `teacher.status_unknown` | 상태 확인 불가 | Status unavailable |
| teacher | `teacher.analysis_status_error` | 실행 상태를 확인할 수 없습니다. 분석을 새로 실행하지 않고 상태를 다시 확인하세요. | Run status is unknown. Check status again without starting a new analysis. |
| teacher | `teacher.analysis_requesting` | 분석을 요청하고 있습니다. | Requesting analysis. |
| teacher | `teacher.analysis_request_conflict` | 요청 입력이 변경되어 재전송할 수 없습니다. 결과를 새로고침하여 확인하세요. | Request inputs changed and cannot be resent. Refresh the result to check. |
| teacher | `teacher.analysis_busy_found` | 진행 중인 분석 실행의 상태를 확인했습니다. | The active analysis run status has been retrieved. |
| teacher | `teacher.analysis_plan_changed` | 계획이 변경되었습니다. 새 범위를 확인하고 실행을 다시 확인하세요. | The plan changed. Review the new coverage and confirm execution again. |
| teacher | `teacher.analysis_plan_limit` | 분석 계획의 한도를 초과해 실행하지 않았습니다. | The analysis plan exceeds the limits and was not executed. |
| teacher | `teacher.analysis_request_result` | 분석 요청 결과를 확인하세요. | Review the analysis request result. |
| teacher | `teacher.request_unknown` | 요청 결과를 확인할 수 없습니다. 같은 요청을 다시 전송할 수 있습니다. | The request outcome is unknown. You can resend the same request. |
| teacher | `teacher.analysis_superseded` | 이 요청의 보고서는 이후 분석으로 대체되었습니다. 최신 결과는 세션 분석 화면에서 확인하세요. | This request’s report was superseded by a later analysis. View the latest result on the session analysis page. |
| teacher | `teacher.preserved_legacy` | 과거 분석을 | Historical analysis |
| teacher | `teacher.preserved_degraded` | 이전 부분 분석을 | Previous degraded analysis |
| teacher | `teacher.preserved_ok` | 이전 정상 결과를 | Previous complete result |
| teacher | `teacher.analysis_preserved_running` | 새 분석이 진행 중입니다. 아래 채택된 보고서는 새 정상 결과가 검증될 때까지 유지됩니다. | A new analysis is running. The accepted report below is preserved until a new complete result is validated. |
| teacher | `teacher.analysis_retry_notice` | 분석을 완료하지 못했습니다. 다시 시도할 수 있습니다. | Analysis could not be completed. You can retry. |
| teacher | `teacher.analysis_failed_notice` | 분석을 완료하지 못했습니다. 기존 결과와 허용된 행동을 확인하세요. | Analysis could not be completed. Review the saved result and available actions. |
| teacher | `teacher.analysis_empty_coverage` | 분석 가능 범위: 0개 메시지 · 호출 없이 안내합니다. | Analysis coverage: 0 messages · no AI calls made. |
| teacher | `teacher.legacy_read_only` | 과거 자료 · 읽기 전용 · 재분석할 수 없습니다. | Historical data · read only · reanalysis unavailable. |
| teacher | `teacher.cancel_cost_notice` | 취소해도 이미 발생한 제공자 비용은 되돌릴 수 없습니다. | Cancellation cannot reverse provider costs already incurred. |
| teacher | `teacher.cancel_analysis` | 진행 중 분석 취소 | Cancel running analysis |
| teacher | `teacher.check_analysis_status` | 실행 상태 다시 확인 | Check run status again |
| teacher | `teacher.plan_blocked` | 분석 계획 불가 | Analysis planning blocked |
| teacher | `teacher.plan_confirm` | 분할 실행 전 확인 | Confirm before running chunks |
| teacher | `teacher.estimate_notice` | 추정치이며 실제 사용량이나 비용을 보장하지 않습니다. | These are estimates and do not guarantee actual usage or cost. |
| teacher | `teacher.preserve_notice` | 기존 정상 결과는 새 정상 결과가 검증될 때까지 보존합니다. 부분 분석이나 실패로 덮어쓰지 않습니다. | The existing complete result is preserved until a new complete result is validated. Degraded or failed analysis does not overwrite it. |
| teacher | `teacher.unit_too_large` | 한 입력 단위가 분석 한도를 초과합니다. 대화를 줄여서 실행하지 않습니다. | One input unit exceeds the analysis limit. The conversation will not be shortened to run analysis. |
| teacher | `teacher.too_many_chunks` | 대화가 최대 8개 분할을 초과합니다. | The conversation exceeds the maximum of 8 chunks. |
| teacher | `teacher.merge_too_large` | 종합할 분석 결과가 한도를 초과합니다. | The analysis results exceed the synthesis limit. |
| teacher | `teacher.unknown_limits` | 분석에 필요한 모델 한도를 확인할 수 없습니다. | The model limits required for analysis are unknown. |
| teacher | `teacher.catalog_limit_conflict` | 카탈로그의 모델 한도가 검증된 한도보다 작아 실행할 수 없습니다. | The catalog model limits are below the verified limits. Analysis cannot run. |
| teacher | `teacher.output_limit_missing` | 저장된 출력 상한이 없어 분석 계획을 만들 수 없습니다. | No output limit is saved. An analysis plan cannot be created. |
| teacher | `teacher.output_limit_exceeded` | 저장된 출력 상한이 모델의 최대 출력 한도를 초과합니다. | The saved output limit exceeds the model’s maximum output limit. |
| teacher | `teacher.plan_unknown` | 분석 범위와 한도를 확인할 수 없어 실행하지 않습니다. | Analysis coverage and limits are unknown. Analysis will not run. |
| teacher | `teacher.run_chunks` | 확인하고 분할 실행 | Confirm and run chunks |
| teacher | `teacher.cancel_chunks` | 분할 실행 취소 | Cancel chunk execution |
| teacher | `teacher.chunks_cancelled` | 분할 분석을 실행하지 않았습니다. 기존 결과는 유지됩니다. | Chunk analysis was not started. Existing results are preserved. |
| teacher | `teacher.view_plan` | 분할 계획 다시 보기 | View chunk plan again |
| teacher | `teacher.retry_analysis` | 분석 재시도 | Retry analysis |
| teacher | `teacher.regenerate_analysis` | 분석 재생성 | Regenerate analysis |
| teacher | `teacher.run_analysis` | 분석 실행 | Run analysis |
| teacher | `teacher.comparison_report` | 검증된 비교 분석 | Validated comparison analysis |
| teacher | `teacher.accepted_report` | 채택된 보고서 | Accepted report |
| teacher | `teacher.coverage` | 분석 범위 | Analysis coverage |
| teacher | `teacher.none` | 없음 | None |
| teacher | `teacher.non_analyzable` | 비분석 | Not analyzable |
| teacher | `teacher.unclassified` | 미분류 | Unclassified |
| teacher | `teacher.missing_classification` | 분류 누락 | Missing classification |
| teacher | `teacher.invalid_classification` | 검증 제외 | Excluded by validation |
| teacher | `teacher.legacy_coverage` | 원문 근거·분석 범위: 알 수 없음 · 과거 결과를 그대로 표시합니다. | Source evidence and analysis coverage: unknown · historical results shown unchanged. |
| teacher | `teacher.legacy_classification` | 과거 분류 사용 여부: 알 수 없음 | Historical classification setting: unknown |
| teacher | `teacher.zero_denominator` | 비율 산정 불가 · 분류 분모 0개 | Percentage unavailable · classification denominator is 0 |
| teacher | `teacher.percentage_unknown` | 비율 산정 불가 | Percentage unavailable |
| teacher | `teacher.findings_title` | 오개념 관찰 | Misconception findings |
| teacher | `teacher.findings_notice` | 학생 발화에 관한 관찰이며, 교사 개입의 인과 효과나 학생 능력 점수가 아닙니다. | These are observations of student statements, not causal effects of teacher intervention or scores of student ability. |
| teacher | `teacher.maintained` | 오개념 유지 | Misconception maintained |
| teacher | `teacher.changed` | 관찰 가능한 변화 | Observable change |
| teacher | `teacher.deviated` | 설정 이탈 | Deviation from the scenario |
| teacher | `teacher.insufficient_evidence` | 판단 근거 부족 | Insufficient evidence |
| teacher | `teacher.legacy_findings` | 과거 오개념 관찰: 알 수 없음 | Historical misconception findings: unknown |
| teacher | `teacher.no_findings` | 검증된 오개념 관찰 근거가 없습니다. | No validated evidence for misconception findings. |
| teacher | `teacher.message_classifications` | 발화별 분류 | Message classifications |
| teacher | `teacher.analysis_load_failed` | 분석 결과를 불러올 수 없습니다. | Could not load analysis results. |
| teacher | `teacher.collapse_analysis` | 상세 분석 접기 | Collapse detailed analysis |
| teacher | `teacher.active_download_unavailable` | 진행중 세션 다운로드 불가 | Active sessions cannot be downloaded |
| teacher | `teacher.row_legacy` | 과거 자료 · 읽기 전용 | Historical data · read only |
| teacher | `teacher.confirm_end_analysis` | 세션을 종료하고 분석하시겠습니까? | End and analyze this session? |
| teacher | `teacher.confirm_delete_session` | 세션을 삭제하시겠습니까? (복구 불가) | Delete this session? (Cannot be undone) |
| teacher | `teacher.end` | 종료 | End |
| teacher | `teacher.analysis_result_action` | 분석결과 | Analysis results |
| teacher | `teacher.delete` | 삭제 | Delete |
| teacher | `teacher.select_session` | 세션 {id} 선택 | Select session {id} |
| teacher | `teacher.student_storage_failed` | 학생 응답 저장에 실패했습니다. | Could not save the student response. |
| teacher | `teacher.mentor_check_execution` | 멘토 실행 상태를 확인해주세요. | Check the mentor run status. |
| teacher | `teacher.user_not_found` | 사용자를 찾을 수 없습니다. | User not found |
| teacher | `teacher.chunk_partial_notice` | 부분 분석: 검증된 {reviewed} / {total}개 메시지의 결과입니다. 완료·실패·미실행 범위는 분석 범위에서 확인하세요. | Partial analysis: validated results for {reviewed} / {total} messages. Check analysis coverage for completed, failed and unprocessed ranges. |
| teacher | `teacher.no_dialogue_feedback` | 대화가 없어 분석할 수 없습니다. | There is no conversation to analyze. |

## admin (845)

| area | key | ko | en |
| --- | --- | --- | --- |
| admin | `admin.archive_sha_256` | 원문 보관본 SHA-256 | Archive SHA-256 |
| admin | `admin.high_level` | 높음 | High |
| admin | `admin.low_level` | 낮음 | Low |
| admin | `admin.admin_dashboard_misconception_dialogue_simulator` | 관리자 대시보드 - 오개념 교정 대화 시뮬레이터 | Admin dashboard - Misconception Dialogue Simulator |
| admin | `admin.dashboard` | 관리자 대시보드 | Admin dashboard |
| admin | `admin.ai_connections_and_models` | AI 연결·모델 | AI connections and models |
| admin | `admin.provider_connections_role_tests_and_authoring_defaults` | 제공자 연결, 역할 시험 및 작성 기본값 | Provider connections, role tests and authoring defaults |
| admin | `admin.scenario_management` | 시나리오 관리 | Scenario management |
| admin | `admin.create_and_edit_dialogue_scenarios` | 대화 시나리오 생성 및 편집 | Create and edit dialogue scenarios |
| admin | `admin.model_comparison` | 모델 비교 | Model comparison |
| admin | `admin.test_models_and_options_with_common_context` | 동일 맥락으로 모델·옵션 시험 | Test models and options with common context |
| admin | `admin.user_conversations` | 사용자 대화 관리 | User conversations |
| admin | `admin.download_conversations_by_user` | 사용자별 대화 다운로드 | Download conversations by user |
| admin | `admin.user_management` | 사용자 관리 | User management |
| admin | `admin.create_edit_and_delete_users` | 사용자 생성, 수정 및 삭제 | Create, edit and delete users |
| admin | `admin.group_management` | 그룹 관리 | Group management |
| admin | `admin.manage_user_groups_and_scenario_access` | 사용자 그룹 및 시나리오 접근 관리 | Manage user groups and scenario access |
| admin | `admin.about_management` | 소개 관리 | About management |
| admin | `admin.add_and_edit_developer_and_administrator_information` | 개발자 및 관리자 정보 등록·수정 | Add and edit developer and administrator information |
| admin | `admin.scenario_management_admin` | 시나리오 관리 - 관리자 | Scenario management - Admin |
| admin | `admin.choose_a_saved_scenario_to_compare` | 비교할 저장 시나리오 선택 | Choose a saved scenario to compare |
| admin | `admin.write_publish_and_manage_scenario_settings` | 통합 설정 작성, 게시 및 관리 | Write, publish and manage scenario settings |
| admin | `admin.whole_model_comparison` | 전체 모델 비교 | Whole-model comparison |
| admin | `admin.comparison_history` | 비교 이력 | Comparison history |
| admin | `admin.create_scenario_draft` | 통합 초안 작성 | Create scenario draft |
| admin | `admin.search_scenarios` | 시나리오 검색 | Search scenarios |
| admin | `admin.search_this_page` | 현재 페이지에서 검색... | Search this page... |
| admin | `admin.publication_status` | 게시 상태 | Publication status |
| admin | `admin.all_publication_states` | 모든 게시 상태 | All publication states |
| admin | `admin.draft` | 초안 | Draft |
| admin | `admin.publish` | 게시 | Publish |
| admin | `admin.activation_status` | 활성 상태 | Activation status |
| admin | `admin.all_activation_states` | 모든 활성 상태 | All activation states |
| admin | `admin.active` | 활성 | Active |
| admin | `admin.inactive` | 비활성 | Inactive |
| admin | `admin.search_and_filters_apply_only_to_scenarios_on_this_page` | 검색과 필터는 현재 페이지의 시나리오에만 적용됩니다. | Search and filters apply only to scenarios on this page. |
| admin | `admin.no_scenarios_on_this_page_match_your_search_and_filters` | 현재 페이지에서 검색·필터에 맞는 시나리오가 없습니다. | No scenarios on this page match your search and filters. |
| admin | `admin.scenarios` | 시나리오 목록 | Scenarios |
| admin | `admin.review_required` | 검토 필요 | Review required |
| admin | `admin.conversion_required` | 변환 필요 | Conversion required |
| admin | `admin.session` | 세션 | Session |
| admin | `admin.conversations` | 대화목록 | Conversations |
| admin | `admin.edit` | 수정 | Edit |
| admin | `admin.delete` | 삭제 | Delete |
| admin | `admin.no_results_on_this_page` | 이 페이지에 결과가 없습니다. | No results on this page. |
| admin | `admin.no_scenarios` | 시나리오가 없습니다 | No scenarios |
| admin | `admin.list_pages` | 목록 페이지 | List pages |
| admin | `admin.total` | 총 | Total |
| admin | `admin.previous` | 이전 | Previous |
| admin | `admin.next` | 다음 | Next |
| admin | `admin.scenario` | 시나리오 | Scenario |
| admin | `admin.another_administrator_made_changes_reload_and_check_again` | 다른 관리자가 변경했습니다. 새로고침 후 확인하세요. | Another administrator made changes. Reload and check again. |
| admin | `admin.deletion_failed` | 삭제에 실패했습니다. | Deletion failed. |
| admin | `admin.scenario_editor_admin` | 시나리오 작성 - 관리자 | Scenario editor - Admin |
| admin | `admin.model_settings_internal_execution` | 모델 설정 · 내부 실행용 | model settings · Internal execution |
| admin | `admin.provider_model` | 제공자 / 모델 | provider / model |
| admin | `admin.saved_options_are_not_merged_with_current_defaults_changing_the_model_displays_its_new_default_optio` | 저장된 옵션은 현재 기본값과 합치지 않습니다. 모델을 변경하면 새 기본 옵션을 표시합니다. | Saved options are not merged with current defaults. Changing the model displays its new default options. |
| admin | `admin.scenario_editor` | 시나리오 작성 | Scenario editor |
| admin | `admin.frozen_comparison_conditions_are_open_in_a_new_draft_a_scenario_is_created_only_when_you_save_or_pub` | 비교의 고정 조건을 새 초안에 열었습니다. 별도 저장 또는 게시 전에는 시나리오가 생성되지 않습니다. | Frozen comparison conditions are open in a new draft. A scenario is created only when you save or publish. |
| admin | `admin.write_public_content_and_internal_instructions_separately_use_pseudonyms_and_educational_personas_in` | 공개 내용과 내부 지시를 나누어 작성하세요. 실제 학생의 식별 정보 대신 가명·교육 페르소나를 사용하세요. | Write public content and internal instructions separately. Use pseudonyms and educational personas instead of identifying real students. |
| admin | `admin.items_to_complete_before_publication` | 게시 전 보완 항목 | Items to complete before publication |
| admin | `admin.reload_latest_version` | 최신 내용 다시 불러오기 | Reload latest version |
| admin | `admin.conversion_review` | 변환 검토 | Conversion review |
| admin | `admin.review_legacy_scenario_conversion` | 기존 시나리오 변환 검토 | Review legacy scenario conversion |
| admin | `admin.this_migration_evidence_is_for_administrators_all_legacy_scenarios_are_drafts_requiring_review_origi` | 관리자 전용 이전 근거입니다. 모든 기존 시나리오는 검토가 필요한 초안입니다. 원문을 공개 소개로 자동 사용하지 않습니다. | This migration evidence is for administrators. All legacy scenarios are drafts requiring review. Original text is not automatically used as a public profile. |
| admin | `admin.original` | 원문 | Original |
| admin | `admin.converted_value` | 변환값 | Converted value |
| admin | `admin.admin_only_original_archive_reference` | 관리자 전용 원문 archive 참조 | Admin-only original archive reference |
| admin | `admin.original_sha_256` | 원문 SHA-256 | Original SHA-256 |
| admin | `admin.converted_value_sha_256` | 변환값 SHA-256 | Converted value SHA-256 |
| admin | `admin.deployment_settings_source_capture_time` | 배포 설정 출처 / 수집 시각 | Deployment settings source / Capture time |
| admin | `admin.legacy_subcall_settings` | 이전 하위 호출 설정 | Legacy subcall settings |
| admin | `admin.see_the_completion_notices_for_review_reasons_acknowledgement_alone_cannot_resolve_missing_required_` | 검토 사유는 보완 안내에서 확인하세요. 누락된 필수 내용은 확인만으로 해소할 수 없습니다. | See the completion notices for review reasons. Acknowledgement alone cannot resolve missing required content. |
| admin | `admin.i_have_reviewed_this_version_s_conversion_warnings` | 이 버전의 변환 경고를 검토했습니다. | I have reviewed this version's conversion warnings. |
| admin | `admin.compare_the_saved_scenario_across_all_roles` | 저장된 시나리오 전체 비교 | Compare the saved scenario across all roles |
| admin | `admin.the_legacy_individual_role_comparison_is_below_start_whole_model_comparisons_from_a_saved_scenario` | 아래는 이전 역할별 단독 비교입니다. 전체 비교는 저장된 시나리오에서 시작하세요. | The legacy individual role comparison is below. Start whole-model comparisons from a saved scenario. |
| admin | `admin.open_model_comparison` | 모델 비교 열기 | Open model comparison |
| admin | `admin.model_comparison_with_common_context` | 동일 맥락 모델 비교 | Model comparison with common context |
| admin | `admin.common_scenario_model` | 시나리오 공통 모델 | Common scenario model |
| admin | `admin.authoring_steps` | 작성 단계 | Authoring steps |
| admin | `admin.basic_information` | 기본 정보 | Basic information |
| admin | `admin.problem_situation` | 문제 상황 | Problem situation |
| admin | `admin.student` | 학생봇 | Student |
| admin | `admin.mentor` | 멘토 | Mentor |
| admin | `admin.post_session_analysis` | 사후 분석 | Post-session analysis |
| admin | `admin.1_basic_information` | 1. 기본 정보 | 1. Basic information |
| admin | `admin.title` | 제목 | Title |
| admin | `admin.subject` | 교과 | Subject |
| admin | `admin.target_grade` | 대상 학년 | Target grade |
| admin | `admin.recent_completed_turns_in_context` | 최근 완료 턴 문맥 상한 | Recent completed turns in context |
| admin | `admin.use_1_100_turns_for_student_and_mentor_context_analysis_uses_the_full_dialogue` | 학생봇·멘토의 문맥 1–100턴. 분석에는 전체 대화를 사용합니다. | Use 1–100 turns for student and mentor context. Analysis uses the full dialogue. |
| admin | `admin.assigned_groups` | 배정 그룹 | Assigned groups |
| admin | `admin.2_problem_situation` | 2. 문제 상황 | 2. Problem situation |
| admin | `admin.visible_to_teachers` | 교사에게 공개 | Visible to teachers |
| admin | `admin.public_problem_situation` | 공개 문제 상황 | Public problem situation |
| admin | `admin.learning_objective` | 학습목표 | Learning objective |
| admin | `admin.3_student` | 3. 학생봇 | 3. Student |
| admin | `admin.student_name` | 학생봇 이름 | Student name |
| admin | `admin.public_student_profile_optional` | 공개 학생 소개 (선택) | Public student profile (optional) |
| admin | `admin.internal_execution_hidden_from_teachers` | 내부 실행용 · 교사에게 비공개 | Internal execution · Hidden from teachers |
| admin | `admin.internal_student_profile_optional` | 내부 학생 프로필 (선택) | Internal student profile (optional) |
| admin | `admin.target_misconception` | 목표 오개념 | Target misconception |
| admin | `admin.student_behavior_instructions` | 학생봇 행동 지시 | Student behavior instructions |
| admin | `admin.tone_participation_response_length_follow_up_questions_conditions_for_changing_misconceptions` | 말투 · 참여도 · 반응 길이 · 되묻기 여부 · 오개념 변화 조건 | Tone · Participation · Response length · Follow-up questions · Conditions for changing misconceptions |
| admin | `admin.4_mentor` | 4. 멘토 | 4. Mentor |
| admin | `admin.mentor_mode` | 멘토 사용 모드 | Mentor mode |
| admin | `admin.off_disabled` | off · 사용 안 함 | off · Disabled |
| admin | `admin.manual_help_button` | manual · 도움 버튼 | manual · Help button |
| admin | `admin.auto_check_after_completed_turns` | auto · 완료 턴 뒤 자동 검사 | auto · Check after completed turns |
| admin | `admin.mentor_name` | 멘토 이름 | Mentor name |
| admin | `admin.mentor_welcome_message_optional` | 멘토 환영문 (선택) | Mentor welcome message (optional) |
| admin | `admin.static_guidance_this_does_not_make_an_ai_call_or_add_a_dialogue_turn` | 정적 안내입니다. AI 호출이나 대화 턴이 아닙니다. | Static guidance; this does not make an AI call or add a dialogue turn. |
| admin | `admin.mentor_behavior_instructions` | 멘토 행동 지시 | Mentor behavior instructions |
| admin | `admin.coaching_approach_tone_feedback_length` | 코칭 방식 · 말투 · 피드백 길이 | Coaching approach · Tone · Feedback length |
| admin | `admin.automatic_intervention_condition` | 자동 개입 조건 | Automatic intervention condition |
| admin | `admin.first_completed_turn_to_check` | 검사 시작 완료 턴 | First completed turn to check |
| admin | `admin.minimum_completed_turns_between_checks` | 검사 사이 최소 완료 턴 | Minimum completed turns between checks |
| admin | `admin.recent_completed_turn_window` | 집계할 최근 완료 턴 | Recent completed turn window |
| admin | `admin.maximum_interventions_in_the_recent_turn_window` | 최근 완료 턴 내 최대 개입 수 | Maximum interventions in the recent turn window |
| admin | `admin.no_intervention_and_accepted_failed_checks_also_consume_the_interval_checks_and_coaching_use_up_to_t` | 미개입·승인된 실패 검사도 간격을 소비합니다. 자동 검사 후 코칭까지 최대 두 호출입니다. 자동 모드의 수동 도움도 최근 턴 상한을 공유합니다. | No-intervention and accepted failed checks also consume the interval. Checks and coaching use up to two calls. Manual help in automatic mode shares the recent-turn cap. |
| admin | `admin.5_post_session_analysis` | 5. 사후 분석 | 5. Post-session analysis |
| admin | `admin.internal_evaluation_hidden_from_teachers` | 내부 평가용 · 교사에게 비공개 | Internal evaluation · Hidden from teachers |
| admin | `admin.analysis_context` | 분석 맥락 | Analysis context |
| admin | `admin.correct_answer_and_expected_understanding` | 정답·기대 이해 | Correct answer and expected understanding |
| admin | `admin.analysis_instructions_and_focus` | 분석 지시·강조점 | Analysis instructions and focus |
| admin | `admin.enable_utterance_classification` | 발화 분류 사용 | Enable utterance classification |
| admin | `admin.rubric_name` | 분류 기준 이름 | Rubric name |
| admin | `admin.rubric_description` | 분류 기준 설명 | Rubric description |
| admin | `admin.classification_display_name` | 분류 표시명 | Classification display name |
| admin | `admin.publication_requires_1_20_complete_rows_ids_may_contain_letters_numbers_underscores_and_hyphens_use_` | 게시할 때 1–20개 완성 행이 필요합니다. ID는 영문·숫자·밑줄·하이픈으로 구성하고 ID와 이름은 중복 없이 작성하세요. | Publication requires 1–20 complete rows. IDs may contain letters, numbers, underscores and hyphens. Use unique IDs and names. |
| admin | `admin.add_rubric_item` | 분류 기준 추가 | Add rubric item |
| admin | `admin.overall_feedback_strengths_improvements_and_alternative_questions_remain_available_when_classificati` | 분류를 끄더라도 총평·강점·개선점·대안 질문은 유지합니다. | Overall feedback, strengths, improvements and alternative questions remain available when classification is disabled. |
| admin | `admin.previous_step` | 이전 단계 | Previous step |
| admin | `admin.next_step` | 다음 단계 | Next step |
| admin | `admin.save_draft` | 초안으로 저장 | Save draft |
| admin | `admin.publication_is_not_available_yet` | 게시 기능 준비 중 | Publication is not available yet |
| admin | `admin.teacher_view_preview` | 교사 화면 미리보기 | Teacher view preview |
| admin | `admin.review_execution_settings` | 실행 설정 확인 | Review execution settings |
| admin | `admin.0_items_to_complete` | 보완 항목 0개 | 0 items to complete |
| admin | `admin.classification` | 분류 | Classification |
| admin | `admin.name` | 이름 | Name |
| admin | `admin.criteria` | 판정 기준 | Criteria |
| admin | `admin.level` | 수준 | Level |
| admin | `admin.unspecified` | 지정하지 않음 | Unspecified |
| admin | `admin.delete_this_rubric_item` | 이 분류 기준을 삭제하시겠습니까? | Delete this rubric item? |
| admin | `admin.mentor_disabled_hidden_input_is_preserved` | 멘토를 사용하지 않습니다. 숨긴 입력은 보존합니다. | Mentor disabled. Hidden input is preserved. |
| admin | `admin.request_help_after_a_completed_student_turn_manual_requests_have_no_total_count_cap` | 완료된 학생 턴에서 도움 버튼으로 요청합니다. 수동 총횟수 상한은 없습니다. | Request help after a completed student turn. Manual requests have no total-count cap. |
| admin | `admin.check_conditions_after_completed_turns_the_help_button_is_also_available` | 완료 턴 뒤 조건을 검사합니다. 도움 버튼으로도 요청할 수 있습니다. | Check conditions after completed turns. The Help button is also available. |
| admin | `admin.selection_required` | 선택 필요 | Selection required |
| admin | `admin.disabled` | 미사용 | Disabled |
| admin | `admin.current_status` | 현재 상태: | Current status: |
| admin | `admin.published` | 게시됨 | Published |
| admin | `admin.version` | 버전 | Version |
| admin | `admin.publish_changes` | 수정 후 게시 | Publish changes |
| admin | `admin.check_your_input` | 입력값을 확인하세요. | Check your input. |
| admin | `admin.items_to_complete` | 보완 항목 | Items to complete |
| admin | `admin.changing_to_a_draft_prevents_teachers_from_starting_new_sessions_save_as_a_draft` | 초안으로 전환하면 교사의 새 세션 시작이 중단됩니다. 초안으로 저장하시겠습니까? | Changing to a draft prevents teachers from starting new sessions. Save as a draft? |
| admin | `admin.another_administrator_made_changes_your_input_is_preserved_latest_version` | 다른 관리자가 변경했습니다. 현재 화면의 입력은 유지됩니다. 최신 버전: | Another administrator made changes. Your input is preserved. Latest version: |
| admin | `admin.draft_saved` | 초안을 저장했습니다. | Draft saved. |
| admin | `admin.publication_succeeded` | 게시했습니다. | Published. |
| admin | `admin.please_log_in` | 로그인이 필요합니다. | Please log in. |
| admin | `admin.check_your_permissions_or_security_token` | 권한 또는 보안 토큰을 확인하세요. | Check your permissions or security token. |
| admin | `admin.scenario_not_found` | 시나리오를 찾을 수 없습니다. | Scenario not found. |
| admin | `admin.could_not_save_your_input_is_preserved` | 저장하지 못했습니다. 입력을 보존했습니다. | Could not save. Your input is preserved. |
| admin | `admin.could_not_confirm_the_save_result_your_input_is_preserved` | 저장 결과를 확인할 수 없습니다. 현재 화면의 입력은 유지됩니다. | Could not confirm the save result. Your input is preserved. |
| admin | `admin.reloading_the_latest_version_discards_unsaved_input_reload` | 최신 내용을 불러오면 저장하지 않은 입력을 잃습니다. 다시 불러오시겠습니까? | Reloading the latest version discards unsaved input. Reload? |
| admin | `admin.cannot_publish_complete_required_fields_and_validate_again_on_the_server` | 게시 불가 · 필수 내용을 보완하고 서버에서 다시 검증해야 합니다. | Cannot publish · Complete required fields and validate again on the server. |
| admin | `admin.review_acknowledged_required_content_and_models_will_be_validated_again_on_publication` | 검토 확인됨 · 게시 시 필수 내용과 모델을 다시 검증합니다. | Review acknowledged · Required content and models will be validated again on publication. |
| admin | `admin.cannot_publish_acknowledge_the_conversion_warnings_first` | 게시 불가 · 변환 경고 검토 확인이 필요합니다. | Cannot publish · Acknowledge the conversion warnings first. |
| admin | `admin.comparison_model_and_per_role_options_applied_save_or_publish_separately` | 비교 모델과 역할별 옵션을 적용했습니다. 별도 저장 또는 게시가 필요합니다. | Comparison model and per-role options applied. Save or publish separately. |
| admin | `admin.unverified` | 미검증 | Unverified |
| admin | `admin.verifying` | 검증 중 | Verifying |
| admin | `admin.succeeded` | 성공 | Succeeded |
| admin | `admin.failed` | 실패 | Failed |
| admin | `admin.reverification_required` | 재검증 필요 | Reverification required |
| admin | `admin.connection_unavailable` | 연결 사용 불가 | Connection unavailable |
| admin | `admin.capability_definition_required` | 기능 정의 필요 | Capability definition required |
| admin | `admin.select_a_common_model` | 공통 모델 선택 필요 | Select a common model |
| admin | `admin.existing_per_role_models_are_preserved_explicitly_select_a_common_model_before_saving` | 기존 역할별 모델을 보존했습니다. 공통 모델을 명시적으로 선택해야 저장할 수 있습니다. | Existing per-role models are preserved. Explicitly select a common model before saving. |
| admin | `admin.all_active_roles_use_the_same_model_options_are_preserved_per_role` | 모든 활성 역할에 같은 모델을 사용합니다. 옵션은 역할별로 보존합니다. | All active roles use the same model. Options are preserved per role. |
| admin | `admin.select_a_model` | 모델 선택 필요 | Select a model |
| admin | `admin.unavailable` | 사용 불가 | Unavailable |
| admin | `admin.default_model_unavailable_select_a_model` | 기본 모델 사용 불가 · 모델 선택 필요 | Default model unavailable · Select a model |
| admin | `admin.registered_model_unavailable` | 등록 모델 사용 불가 | Registered model unavailable |
| admin | `admin.capability_definition_and_provider_catalog_mismatch` | 기능 정의·제공자 목록 불일치 | Capability definition and provider catalog mismatch |
| admin | `admin.model_changed_new_default_options_displayed` | 모델 변경: 새 기본 옵션을 표시했습니다. | Model changed: new default options displayed. |
| admin | `admin.unconfigured` | 미설정 | Unconfigured |
| admin | `admin.available` | 사용 가능 | Available |
| admin | `admin.decryption_failed` | 복호화 실패 | Decryption failed |
| admin | `admin.the_response_was_refused_check_settings_and_test_again` | 응답이 거절되었습니다. 설정을 확인한 후 다시 시험하세요. | The response was refused. Check settings and test again. |
| admin | `admin.the_output_limit_was_reached_check_settings_and_test_again` | 출력 길이 상한에 도달했습니다. 설정을 확인한 후 다시 시험하세요. | The output limit was reached. Check settings and test again. |
| admin | `admin.the_response_is_not_complete_json_check_settings_and_test_again` | 완전한 JSON 응답이 아닙니다. 설정을 확인한 후 다시 시험하세요. | The response is not complete JSON. Check settings and test again. |
| admin | `admin.a_label_utterance_reference_or_quotation_is_invalid_check_settings_and_test_again` | 레이블·발화 참조 또는 인용이 유효하지 않습니다. 설정을 확인한 후 다시 시험하세요. | A label, utterance reference or quotation is invalid. Check settings and test again. |
| admin | `admin.required_results_are_empty_check_settings_and_test_again` | 필요한 결과가 비어 있습니다. 설정을 확인한 후 다시 시험하세요. | Required results are empty. Check settings and test again. |
| admin | `admin.the_response_format_does_not_match_the_role_contract_check_settings_and_test_again` | 응답 형식이 역할 계약과 일치하지 않습니다. 설정을 확인한 후 다시 시험하세요. | The response format does not match the role contract. Check settings and test again. |
| admin | `admin.the_provider_encountered_a_temporary_problem_test_again_shortly` | 제공자에 일시적인 문제가 발생했습니다. 잠시 후 다시 시험하세요. | The provider encountered a temporary problem. Test again shortly. |
| admin | `admin.the_provider_connection_timed_out_check_the_connection` | 제공자 연결 시간이 초과되었습니다. 연결 상태를 확인하세요. | The provider connection timed out. Check the connection. |
| admin | `admin.the_first_response_timed_out_check_the_timeout_settings` | 첫 응답 대기 시간이 초과되었습니다. 시간 제한을 확인하세요. | The first response timed out. Check the timeout settings. |
| admin | `admin.the_total_call_timeout_was_reached_check_the_timeout_settings` | 전체 호출 시간이 초과되었습니다. 시간 제한을 확인하세요. | The total call timeout was reached. Check the timeout settings. |
| admin | `admin.the_concurrent_call_limit_was_reached_wait_for_active_calls_to_finish_before_testing_again` | 동시 호출 한도에 도달했습니다. 진행 중인 호출이 끝난 후 다시 시험하세요. | The concurrent call limit was reached. Wait for active calls to finish before testing again. |
| admin | `admin.unsupported_saved_value` | 지원하지 않는 저장 값 | Unsupported saved value |
| admin | `admin.capability_definition` | 기능 정의: | Capability definition: |
| admin | `admin.official_verification` | 공식 확인 | Official verification |
| admin | `admin.default` | 기본값: | Default: |
| admin | `admin.these_saved_options_are_absent_from_the_capability_definition_editing_other_fields_preserves_them_ch` | 기능 정의에 없는 저장 옵션입니다. 다른 필드 수정으로 삭제하지 않으며, 제거를 선택한 뒤 저장해야 삭제됩니다. | These saved options are absent from the capability definition. Editing other fields preserves them. Choose removal and save to delete them. |
| admin | `admin.remove_undefined_saved_options` | 정의에 없는 저장 옵션 제거 | Remove undefined saved options |
| admin | `admin.master_key_required_restore_the_original_infrastructure_master_key_and_version_saving_keys_and_ai_ca` | 마스터 키 설정 필요: 인프라의 원래 마스터 키와 버전을 복원하세요. 키 저장과 AI 호출은 사용할 수 없습니다. | Master key required: restore the original infrastructure master key and version. Saving keys and AI calls are unavailable. |
| admin | `admin.provider_connections` | 제공자 연결 | Provider connections |
| admin | `admin.1_provider_connections` | 1. 제공자 연결 | 1. Provider connections |
| admin | `admin.non_generation_verification_and_catalog_refresh_are_separate_from_role_verification_and_educational_` | 비생성 확인과 모델 목록 갱신은 역할 검증·교육 품질 승인과 별개입니다. | Non-generation verification and catalog refresh are separate from role verification and educational quality approval. |
| admin | `admin.key` | 키 | Key |
| admin | `admin.registered` | 등록됨 | Registered |
| admin | `admin.not_registered` | 미등록 | Not registered |
| admin | `admin.latest_non_generation_verification` | 최근 비생성 확인: | Latest non-generation verification: |
| admin | `admin.none` | 없음 | None |
| admin | `admin.restore_the_original_master_key_and_version_if_recovery_is_impossible_delete_and_register_the_key_ag` | 원래 마스터 키·버전을 복원하세요. 복구할 수 없다면 현재 비밀번호로 키를 삭제한 후 재등록하세요. | Restore the original master key and version. If recovery is impossible, delete and register the key again using your current password. |
| admin | `admin.latest_non_generation_verification_failed_check_connection_settings_and_access_permissions` | 최근 비생성 확인 실패. 연결 설정과 접근 권한을 확인하세요. | Latest non-generation verification failed. Check connection settings and access permissions. |
| admin | `admin.replace_key` | 키 교체 | Replace key |
| admin | `admin.save_key` | 키 저장 | Save key |
| admin | `admin.disable` | 비활성화 | Disable |
| admin | `admin.re_enable` | 재활성화 | Re-enable |
| admin | `admin.delete_key` | 키 삭제 | Delete key |
| admin | `admin.verify_without_generation_and_refresh_catalog` | 비생성 확인·목록 갱신 | Verify without generation and refresh catalog |
| admin | `admin.the_model_catalog_is_not_available_yet` | 모델 목록은 아직 제공되지 않습니다. | The model catalog is not available yet. |
| admin | `admin.model_catalog_no_lookup_recorded` | 모델 목록: 조회 기록 없음 | Model catalog: no lookup recorded |
| admin | `admin.model_catalog` | 모델 목록: | Model catalog: |
| admin | `admin.stale_catalog` | 오래된 목록 | Stale catalog |
| admin | `admin.recent_catalog` | 최근 목록 | Recent catalog |
| admin | `admin.last_refreshed` | 마지막 갱신 | Last refreshed |
| admin | `admin.this_catalog_is_for_reference_it_does_not_guarantee_access_or_successful_role_verification` | 참고 목록입니다. 접근 가능 여부나 역할 검증 성공을 보장하지 않습니다. | This catalog is for reference. It does not guarantee access or successful role verification. |
| admin | `admin.lifecycle_unknown` | 수명주기 미확인 | Lifecycle unknown |
| admin | `admin.stable` | 안정 | Stable |
| admin | `admin.release_status_unknown` | 릴리스 상태 미확인 | Release status unknown |
| admin | `admin.choose_model_id` | 모델 ID 선택 | Choose model ID |
| admin | `admin.register_selected_model` | 선택 모델 등록 | Register selected model |
| admin | `admin.enter_model_id_manually` | 모델 ID 직접 입력 | Enter model ID manually |
| admin | `admin.model_settings` | 모델 설정 | Model settings |
| admin | `admin.2_model_settings_and_role_verification` | 2. 모델 설정·역할 검증 | 2. Model settings and role verification |
| admin | `admin.check_response_format_verification_separately_for_each_role_test_success_does_not_guarantee_educatio` | 각 역할의 응답 형식 검증을 따로 확인하세요. 시험 성공은 교육적 품질 보증이 아닙니다. | Check response-format verification separately for each role. Test success does not guarantee educational quality. |
| admin | `admin.no_registered_models_choose_a_catalog_entry_under_a_provider_connection_or_enter_a_model_id_manually` | 등록한 모델이 없습니다. 제공자 연결에서 목록을 선택하거나 모델 ID를 직접 입력해 등록하세요. | No registered models. Choose a catalog entry under a provider connection or enter a model ID manually. |
| admin | `admin.model_registration_and_role_tests_are_not_available_yet` | 모델 등록·역할 시험은 아직 제공되지 않습니다. | Model registration and role tests are not available yet. |
| admin | `admin.role_tests_are_not_available_yet` | 역할 시험은 아직 제공되지 않습니다. | Role tests are not available yet. |
| admin | `admin.capability_definition_available` | 기능 정의 있음 | Capability definition available |
| admin | `admin.edit_display_name_activation_and_options` | 표시명·활성·옵션 수정 | Edit display name, activation and options |
| admin | `admin.capability_definition_and_provider_catalog_do_not_match_role_tests_and_execution_are_unavailable` | 기능 정의·제공자 목록 불일치: 역할 시험과 실행을 사용할 수 없습니다. | Capability definition and provider catalog do not match: role tests and execution are unavailable. |
| admin | `admin.role_verification` | 역할 검증 | Role verification |
| admin | `admin.latest_role_verification` | 최근 역할 검증: | Latest role verification: |
| admin | `admin.the_role_response_format_test_failed_check_settings_and_test_again` | 역할 응답 형식 시험에 실패했습니다. 설정을 확인한 후 다시 시험하세요. | The role response-format test failed. Check settings and test again. |
| admin | `admin.the_test_output_limit_conflicts_with_saved_options_explicitly_update_the_saved_options` | 시험 출력 상한 또는 저장 옵션 제약과 충돌합니다. 저장 옵션을 명시적으로 수정하세요. | The test output limit conflicts with saved options. Explicitly update the saved options. |
| admin | `admin.this_role_test_is_not_available_yet` | 이 역할 시험은 아직 제공되지 않습니다. | This role test is not available yet. |
| admin | `admin.role_tests_require_a_capability_definition` | 기능 정의가 없어 역할 시험을 사용할 수 없습니다. | Role tests require a capability definition. |
| admin | `admin.resolve_the_capability_definition_and_provider_catalog_mismatch_before_testing` | 기능 정의·제공자 목록 불일치를 해결한 후 시험하세요. | Resolve the capability definition and provider catalog mismatch before testing. |
| admin | `admin.restore_the_master_key_before_testing` | 마스터 키를 복원한 후 시험하세요. | Restore the master key before testing. |
| admin | `admin.resolve_the_connection_key_decryption_failure_before_testing` | 연결 키 복호화 실패를 해결한 후 시험하세요. | Resolve the connection key decryption failure before testing. |
| admin | `admin.register_a_provider_key_before_testing` | 제공자 키를 등록한 후 시험하세요. | Register a provider key before testing. |
| admin | `admin.re_enable_the_provider_connection_before_testing` | 제공자 연결을 재활성화한 후 시험하세요. | Re-enable the provider connection before testing. |
| admin | `admin.check_provider_connection_settings_before_testing` | 제공자 연결 설정을 확인한 후 시험하세요. | Check provider connection settings before testing. |
| admin | `admin.update_the_test_output_limit_and_saved_options_before_testing` | 시험 출력 상한·저장 옵션을 수정한 후 시험하세요. | Update the test output limit and saved options before testing. |
| admin | `admin.you_can_check_or_cancel_the_running_test` | 진행 중인 시험을 조회하거나 취소할 수 있습니다. | You can check or cancel the running test. |
| admin | `admin.test` | 시험 | Test |
| admin | `admin.check_progress` | 진행 조회 | Check progress |
| admin | `admin.cancel_test` | 시험 취소 | Cancel test |
| admin | `admin.authoring_defaults_and_call_settings` | 작성 기본값·호출 설정 | Authoring defaults and call settings |
| admin | `admin.3_authoring_defaults_and_call_settings` | 3. 작성 기본값·호출 설정 | 3. Authoring defaults and call settings |
| admin | `admin.authoring_defaults_apply_only_to_new_scenarios_existing_scenarios_and_sessions_remain_unchanged_unav` | 작성 기본값은 새 시나리오 작성에만 사용합니다. 기존 시나리오와 세션은 변경하지 않습니다. 사용 불가 기본값을 다른 모델로 자동 대체하지 않습니다. | Authoring defaults apply only to new scenarios. Existing scenarios and sessions remain unchanged. Unavailable defaults are not automatically replaced. |
| admin | `admin.authoring_defaults_and_call_settings_are_not_available_yet` | 작성 기본값·호출 설정은 아직 제공되지 않습니다. | Authoring defaults and call settings are not available yet. |
| admin | `admin.default_model_unavailable` | 기본 모델 사용 불가 | Default model unavailable |
| admin | `admin.no_default` | 기본값 없음 | No default |
| admin | `admin.edit_authoring_defaults_and_call_settings` | 작성 기본값·호출 설정 수정 | Edit authoring defaults and call settings |
| admin | `admin.item_count` | {count}개 | {count} |
| admin | `admin.scenario_delete_confirm` | "{title}" 시나리오를 삭제하시겠습니까?<br>관련 세션 {count}개도 함께 숨겨집니다. | Delete scenario "{title}"?<br>Its {count} related sessions will also be hidden. |
| admin | `admin.about_management_admin` | 소개 관리 - 관리자 | About management - Admin |
| admin | `admin.add_contributor` | + 새 등록 | + Add contributor |
| admin | `admin.contributors` | 소개 목록 | Contributors |
| admin | `admin.order` | 순서 | Order |
| admin | `admin.affiliation` | 소속 | Affiliation |
| admin | `admin.contact` | 연락처 | Contact |
| admin | `admin.history` | 이력 | History |
| admin | `admin.actions` | 작업 | Actions |
| admin | `admin.no_contributors_have_been_added` | 등록된 정보가 없습니다. | No contributors have been added. |
| admin | `admin.add_contributor_heading` | 새 등록자 추가 | Add contributor |
| admin | `admin.close` | 닫기 | Close |
| admin | `admin.phone_number_optional` | 전화번호 (선택) | Phone number (optional) |
| admin | `admin.email_optional` | 이메일 (선택) | Email (optional) |
| admin | `admin.display_order` | 표시 순서 | Display order |
| admin | `admin.cancel` | 취소 | Cancel |
| admin | `admin.save` | 저장 | Save |
| admin | `admin.edit_contributor` | 등록자 수정 | Edit contributor |
| admin | `admin.confirm_deletion` | 삭제 확인 | Confirm deletion |
| admin | `admin.analysis_management_admin` | 분석 관리 - 관리자 | Analysis management - Admin |
| admin | `admin.analysis_management` | 분석 관리 | Analysis management |
| admin | `admin.view_and_manage_question_analysis_results` | 전체 질문 분석 결과 조회 및 관리 | View and manage question analysis results |
| admin | `admin.scenario_filter` | 시나리오 필터 | Scenario filter |
| admin | `admin.all_scenarios` | 모든 시나리오 | All scenarios |
| admin | `admin.label` | 레이블 | Label |
| admin | `admin.label_filter` | 레이블 필터 | Label filter |
| admin | `admin.all_labels` | 모든 레이블 | All labels |
| admin | `admin.start_date` | 시작일 | Start date |
| admin | `admin.end_date` | 종료일 | End date |
| admin | `admin.apply_filters` | 필터 적용 | Apply filters |
| admin | `admin.reset` | 초기화 | Reset |
| admin | `admin.total_analyses` | 총 분석 수 | Total analyses |
| admin | `admin.average_confidence` | 평균 신뢰도 | Average confidence |
| admin | `admin.not_available` | 산정 불가 | Not available |
| admin | `admin.most_frequent_type` | 가장 많은 유형 | Most frequent type |
| admin | `admin.analyses` | 분석 목록 | Analyses |
| admin | `admin.question` | 질문 | Question |
| admin | `admin.confidence` | 신뢰도 | Confidence |
| admin | `admin.time` | 시간 | Time |
| admin | `admin.analysis_actions` | 액션 | Actions |
| admin | `admin.historical_data_read_only` | · 과거 자료 · 읽기 전용 | · Historical data · Read only |
| admin | `admin.view_details` | 상세보기 | View details |
| admin | `admin.no_analysis_results` | 분석 결과가 없습니다. | No analysis results. |
| admin | `admin.analysis_details` | 분석 상세 | Analysis details |
| admin | `admin.user_conversations_admin` | 사용자 대화 관리 - 관리자 | User conversations - Admin |
| admin | `admin.download_conversation_history_by_user` | 사용자별 대화 내역 다운로드 | Download conversation history by user |
| admin | `admin.user_conversations_table` | 사용자 대화 목록 | User conversations |
| admin | `admin.nickname` | 닉네임 | Nickname |
| admin | `admin.student_number` | 학번 | Student number |
| admin | `admin.session_count` | 세션 수 | Session count |
| admin | `admin.last_session` | 마지막 세션 | Last session |
| admin | `admin.download_all_as_csv` | 전체 CSV 다운로드 | Download all as CSV |
| admin | `admin.all` | 전체 | All |
| admin | `admin.download_individual_sessions` | 개별 세션 다운로드 | Download individual sessions |
| admin | `admin.individual` | 개별 | Individual |
| admin | `admin.no_completed_sessions` | 완료된 세션 없음 | No completed sessions |
| admin | `admin.no_users_have_conversation_history` | 대화 기록이 있는 사용자가 없습니다. | No users have conversation history. |
| admin | `admin.loading` | 로딩 중... | Loading... |
| admin | `admin.dialogue_sessions_admin` | 대화 세션 관리 - 관리자 | Dialogue sessions - Admin |
| admin | `admin.dialogue_sessions` | 대화 세션 관리 | Dialogue sessions |
| admin | `admin.view_end_and_delete_sessions` | 세션 조회, 종료, 삭제 | View, end and delete sessions |
| admin | `admin.teacher_filter` | 교사 필터 | Teacher filter |
| admin | `admin.all_teachers` | 모든 교사 | All teachers |
| admin | `admin.download_all` | 전체 다운로드 | Download all |
| admin | `admin.download_selected` | 선택 다운로드 | Download selected |
| admin | `admin.0_selected` | 0개 선택됨 | 0 selected |
| admin | `admin.sessions` | 세션 목록 | Sessions |
| admin | `admin.select_all_sessions` | 모든 세션 선택 | Select all sessions |
| admin | `admin.teacher` | 교사 | Teacher |
| admin | `admin.start_time` | 시작 시간 | Start time |
| admin | `admin.end_time` | 종료 시간 | End time |
| admin | `admin.status` | 상태 | Status |
| admin | `admin.completed` | 완료 | Completed |
| admin | `admin.in_progress` | 진행중 | In progress |
| admin | `admin.end` | 종료 | End |
| admin | `admin.no_results_for_the_current_filters_or_page` | 현재 필터 또는 페이지에 결과가 없습니다. | No results for the current filters or page. |
| admin | `admin.no_sessions` | 세션이 없습니다. | No sessions. |
| admin | `admin.first` | 처음 | First |
| admin | `admin.last` | 마지막 | Last |
| admin | `admin.user_management_admin` | 사용자 관리 - 관리자 | User management - Admin |
| admin | `admin.download_csv_template` | CSV 양식 다운로드 | Download CSV template |
| admin | `admin.bulk_add` | 일괄 추가 | Bulk add |
| admin | `admin.new_user` | + 새 사용자 | + New user |
| admin | `admin.search_users` | 사용자 검색... | Search users... |
| admin | `admin.all_roles` | 모든 역할 | All roles |
| admin | `admin.admin` | 관리자 | Admin |
| admin | `admin.all_groups` | 모든 그룹 | All groups |
| admin | `admin.no_users_on_this_page_match_your_search_and_filters` | 현재 페이지에서 검색·필터에 맞는 사용자가 없습니다. | No users on this page match your search and filters. |
| admin | `admin.users` | 사용자 목록 | Users |
| admin | `admin.user_id` | 사용자 ID | User ID |
| admin | `admin.role` | 역할 | Role |
| admin | `admin.group` | 그룹 | Group |
| admin | `admin.created` | 생성일 | Created |
| admin | `admin.no_users` | 사용자가 없습니다. | No users. |
| admin | `admin.bulk_add_users` | 일괄 사용자 추가 | Bulk add users |
| admin | `admin.choose_a_csv_file` | CSV 파일을 선택하세요 | Choose a CSV file |
| admin | `admin.format_username_nickname_role_optional_group_optional` | 양식: username, nickname, role(선택), group(선택) | Format: username, nickname, role(optional), group(optional) |
| admin | `admin.choose_file` | 파일 선택 | Choose file |
| admin | `admin.a_random_initial_password_is_issued_for_each_user` | 사용자마다 무작위 초기 비밀번호를 발급합니다. | A random initial password is issued for each user. |
| admin | `admin.bulk_assignment` | 일괄 지정: | Bulk assignment: |
| admin | `admin.choose_role` | 역할 선택... | Choose role... |
| admin | `admin.choose_group` | 그룹 선택... | Choose group... |
| admin | `admin.apply` | 적용 | Apply |
| admin | `admin.bulk_registration_preview` | 일괄 등록 미리보기 | Bulk registration preview |
| admin | `admin.add_user` | + 사용자 추가 | + Add user |
| admin | `admin.available_only_until_this_screen_is_closed` | 이 화면을 닫으면 다시 볼 수 없음 | Available only until this screen is closed |
| admin | `admin.copy_the_initial_passwords_or_save_them_as_csv_to_share_with_users` | 초기 비밀번호를 복사하거나 CSV로 저장해 사용자에게 전달하세요. | Copy the initial passwords or save them as CSV to share with users. |
| admin | `admin.copy_credentials` | 자격 증명 복사 | Copy credentials |
| admin | `admin.download_credentials_csv` | 자격 증명 CSV 다운로드 | Download credentials CSV |
| admin | `admin.one_time_credentials` | 일회 자격 증명 | One-time credentials |
| admin | `admin.initial_password` | 초기 비밀번호 | Initial password |
| admin | `admin.failures` | 실패 목록 | Failures |
| admin | `admin.registration_failures` | 등록 실패 목록 | Registration failures |
| admin | `admin.failure_reason` | 실패 사유 | Failure reason |
| admin | `admin.register` | 등록 | Register |
| admin | `admin.create_user` | 새 사용자 생성 | Create user |
| admin | `admin.letters_numbers_underscores` | 영문/숫자/언더스코어 | Letters/numbers/underscores |
| admin | `admin.password` | 비밀번호 | Password |
| admin | `admin.no_group` | 그룹 없음 | No group |
| admin | `admin.create_user_submit` | 사용자 생성 | Create user |
| admin | `admin.edit_user` | 사용자 수정 | Edit user |
| admin | `admin.reset_password` | 비밀번호 재설정 | Reset password |
| admin | `admin.new_password` | 새 비밀번호 | New password |
| admin | `admin.enter_a_value_to_change_the_password` | 입력하면 비밀번호가 변경됩니다 | Enter a value to change the password |
| admin | `admin.save_changes` | 변경사항 저장 | Save changes |
| admin | `admin.group_management_admin` | 그룹 관리 - 관리자 | Group management - Admin |
| admin | `admin.create_and_manage_user_groups` | 사용자 그룹 생성 및 관리 | Create and manage user groups |
| admin | `admin.new_group` | + 새 그룹 | + New group |
| admin | `admin.groups` | 그룹 목록 | Groups |
| admin | `admin.members` | 멤버 | Members |
| admin | `admin.no_groups` | 그룹이 없습니다 | No groups |
| admin | `admin.no_groups_have_been_added` | 등록된 그룹이 없습니다. | No groups have been added. |
| admin | `admin.create_the_first_group` | + 첫 번째 그룹 만들기 | + Create the first group |
| admin | `admin.create_group` | 새 그룹 생성 | Create group |
| admin | `admin.group_name` | 그룹 이름 | Group name |
| admin | `admin.enter_group_name` | 그룹 이름 입력 | Enter group name |
| admin | `admin.description` | 설명 | Description |
| admin | `admin.group_description_optional` | 그룹 설명 (선택) | Group description (optional) |
| admin | `admin.create_group_submit` | 그룹 생성 | Create group |
| admin | `admin.edit_group` | 그룹 수정 | Edit group |
| admin | `admin.ai_connections_and_models_admin` | AI 연결·모델 - 관리자 | AI connections and models - Admin |
| admin | `admin.provider_connection_model_registration_role_tests_authoring_defaults_and_call_settings` | 제공자 연결 → 모델 등록 → 역할 시험 → 작성 기본값·호출 설정 | Provider connection → Model registration → Role tests → Authoring defaults and call settings |
| admin | `admin.loading_settings` | 설정을 불러오는 중입니다. | Loading settings. |
| admin | `admin.reload_settings` | 설정 다시 불러오기 | Reload settings |
| admin | `admin.selected_action` | 선택한 작업 | Selected action |
| admin | `admin.api_usage_admin` | API 사용량 - 관리자 | API usage - Admin |
| admin | `admin.unknown` | 알 수 없음 | Unknown |
| admin | `admin.record` | 기록 | Record |
| admin | `admin.provider` | 제공자 | Provider |
| admin | `admin.model` | 모델 | Model |
| admin | `admin.purpose` | 목적 | Purpose |
| admin | `admin.attempt` | 시도 | Attempt |
| admin | `admin.time_utc` | 시간 (UTC) | Time (UTC) |
| admin | `admin.input_tokens` | 입력 토큰 | Input tokens |
| admin | `admin.output_tokens` | 출력 토큰 | Output tokens |
| admin | `admin.cache_read_tokens` | 캐시 읽기 토큰 | Cache read tokens |
| admin | `admin.cache_write_tokens` | 캐시 쓰기 토큰 | Cache write tokens |
| admin | `admin.reasoning_tokens` | 추론 토큰 | Reasoning tokens |
| admin | `admin.total_tokens` | 총 토큰 | Total tokens |
| admin | `admin.estimated_cost_usd` | 추정 비용 (USD) | Estimated cost (USD) |
| admin | `admin.pricing_date_and_source` | 단가 기준일·근거 | Pricing date and source |
| admin | `admin.legacy_record` | 기존 기록 | Legacy record |
| admin | `admin.call_attempt` | 호출 시도 | Call attempt |
| admin | `admin.admin_comparison_comparison` | 관리자 비교 (comparison) | Admin comparison (comparison) |
| admin | `admin.refused` | 거절 | Refused |
| admin | `admin.timed_out` | 시간 초과 | Timed out |
| admin | `admin.interrupted` | 중단 | Interrupted |
| admin | `admin.operation_in_progress` | 진행 중 | In progress |
| admin | `admin.error` | 오류: | Error: |
| admin | `admin.usage` | 사용량: | Usage: |
| admin | `admin.complete` | 완전 | Complete |
| admin | `admin.partial_unknown` | 일부 / 알 수 없음 | Partial / Unknown |
| admin | `admin.started` | 시작: | Started: |
| admin | `admin.first_output` | 첫 출력: | First output: |
| admin | `admin.ended` | 종료: | Ended: |
| admin | `admin.retry_delay_ms` | 재시도 대기 (ms): | Retry delay (ms): |
| admin | `admin.source` | 근거: | Source: |
| admin | `admin.no_records` | 기록이 없습니다. | No records. |
| admin | `admin.api_usage` | API 사용량 | API usage |
| admin | `admin.call_attempts_token_usage_and_known_estimated_costs_by_provider` | 제공자별 호출 시도·토큰 사용량·알려진 추정 비용 | Call attempts, token usage and known estimated costs by provider |
| admin | `admin.usage_summary` | 사용량 요약 | Usage summary |
| admin | `admin.known_estimated_cost_total` | 알려진 추정 비용 합계 | Known estimated cost total |
| admin | `admin.generation_attempts_with_unknown_cost` | 미산정 생성 시도 수 | Generation attempts with unknown cost |
| admin | `admin.non_generation_catalog_calls` | 비생성 목록 호출 수 | Non-generation catalog calls |
| admin | `admin.this_subtotal_covers_known_generation_cost_estimates_only_it_excludes_unknown_costs_and_catalog_call` | 생성 호출의 알려진 추정 비용만 더한 부분 합계이며, 전체 과금액이 아닙니다. 미산정 비용과 비생성 목록 호출은 합계에서 제외합니다. | This subtotal covers known generation cost estimates only. It excludes unknown costs and catalog calls and does not represent the full bill. |
| admin | `admin.new_estimates_use_verified_published_paid_rates_actual_charges_may_differ_with_free_usage_credits_di` | 새 추정 비용은 확인된 공시 유료 단가 기준입니다. 무료 이용·크레딧·개별 할인·세금에 따른 실제 청구액은 다를 수 있습니다. | New estimates use verified published paid rates. Actual charges may differ with free usage, credits, discounts and taxes. |
| admin | `admin.legacy_costs_are_preserved_legacy_records_cannot_establish_total_attempts_failures_retries_or_unknow` | 기존 기록의 비용을 보존합니다. 기존 기록만으로는 실패·재시도를 포함한 전체 시도 수와 미산정 건수를 확인할 수 없습니다. | Legacy costs are preserved. Legacy records cannot establish total attempts, failures, retries or unknown costs. |
| admin | `admin.unknown_cost_counts_include_only_recorded_generation_attempts_legacy_records_without_attempt_data_ar` | 미산정 생성 시도 수는 호출 원장에 기록된 시도만 셉니다. 시도 정보가 없는 기존 기록은 이 건수에 포함하지 않습니다. | Unknown-cost counts include only recorded generation attempts. Legacy records without attempt data are excluded. |
| admin | `admin.totals_cover_all_records_the_tables_show_the_latest_100_unobserved_values_appear_as_unknown_rather_t` | 합계·건수는 전체 기록 기준이며, 아래 표는 최근 100건입니다. 관측되지 않은 값은 ‘알 수 없음’으로 표시하며 0으로 대체하지 않습니다. | Totals cover all records; the tables show the latest 100. Unobserved values appear as Unknown rather than zero. |
| admin | `admin.cache_and_reasoning_tokens_are_breakdowns_not_added_to_totals_again_cancellation_or_interruption_doe` | 캐시·추론 토큰은 상세 항목이며 총량에 다시 더하지 않습니다. 취소·중단은 제공자의 과금 중단을 보장하지 않습니다. | Cache and reasoning tokens are breakdowns, not added to totals again. Cancellation or interruption does not guarantee billing stops. |
| admin | `admin.focus_the_table_and_use_the_left_and_right_arrow_keys_to_view_all_columns` | 표에 초점을 두고 좌우 방향키로 모든 열을 확인할 수 있습니다. | Focus the table and use the left and right arrow keys to view all columns. |
| admin | `admin.generation_calls_and_legacy_records` | 생성 호출 및 기존 기록 | Generation calls and legacy records |
| admin | `admin.non_generation_catalog_calls_excluded_from_generation_costs` | 비생성 목록 호출 — 생성 비용 집계에서 제외 | Non-generation catalog calls — excluded from generation costs |
| admin | `admin.scenario_model_comparison_admin` | 시나리오 모델 비교 - 관리자 | Scenario model comparison - Admin |
| admin | `admin.scenario_model_comparison` | 시나리오 모델 비교 | Scenario model comparison |
| admin | `admin.choose_a_saved_scenario` | 저장된 시나리오 선택 | Choose a saved scenario |
| admin | `admin.all_panels_use_the_same_question_and_adopted_dialogue_results_are_retained_for_30_days_after_creatio` | 모든 패널이 같은 질문과 채택된 대화를 사용합니다. 결과는 생성 후 30일 동안 보존합니다. | All panels use the same question and adopted dialogue. Results are retained for 30 days after creation. |
| admin | `admin.adopting_a_student_response_updates_the_canonical_conversation_history_applying_a_model_opens_the_ed` | 학생 응답 채택은 기준 대화 이력을 갱신합니다. 모델 적용은 작성기로 이동하며 별도 저장 또는 게시가 필요합니다. 비교 자료는 수업 기록과 분리됩니다. | Adopting a student response updates the canonical conversation history. Applying a model opens the editor and requires a separate save or publication. Comparison data is separate from classroom records. |
| admin | `admin.canonical_dialogue` | 기준 대화 | Canonical dialogue |
| admin | `admin.comparison_conversation_controls` | 비교 대화 조작 | Comparison conversation controls |
| admin | `admin.common_teacher_question` | 공통 교사 질문 | Common teacher question |
| admin | `admin.results_by_model` | 모델별 결과 | Results by model |
| admin | `admin.conversation_comparison_history` | 전체 비교 이력 | Conversation comparison history |
| admin | `admin.legacy_role_comparison_history` | 이전 역할별 비교 이력 | Legacy role comparison history |
| admin | `admin.a_change_or_running_task_caused_a_conflict_reload_the_settings` | 다른 변경 또는 진행 중 작업과 충돌했습니다. 설정을 다시 불러오세요. | A change or running task caused a conflict. Reload the settings. |
| admin | `admin.check_your_input_or_current_password` | 입력값 또는 현재 비밀번호를 확인하세요. | Check your input or current password. |
| admin | `admin.the_task_limit_has_been_reached_try_again_shortly` | 작업 한도에 도달했습니다. 잠시 후 다시 시도하세요. | The task limit has been reached. Try again shortly. |
| admin | `admin.could_not_complete_the_task_check_connection_settings_and_service_status` | 작업을 완료할 수 없습니다. 연결 설정과 서비스 상태를 확인하세요. | Could not complete the task. Check connection settings and service status. |
| admin | `admin.settings_loaded_opening_this_page_does_not_call_providers` | 설정을 불러왔습니다. 화면 진입은 제공자를 호출하지 않습니다. | Settings loaded. Opening this page does not call providers. |
| admin | `admin.could_not_load_ai_connection_settings_check_that_administrator_connection_management_is_available` | AI 연결 설정을 불러올 수 없습니다. 관리자 연결 기능이 준비되었는지 확인하세요. | Could not load AI connection settings. Check that administrator connection management is available. |
| admin | `admin.task_completed` | 작업을 완료했습니다. | Task completed. |
| admin | `admin.working` | 작업 중입니다. | Working. |
| admin | `admin.reload_the_latest_state` | 최신 상태를 다시 불러오세요. | Reload the latest state. |
| admin | `admin.task_failed` | 작업에 실패했습니다. | Task failed. |
| admin | `admin.change_connection` | 연결 변경 | Change connection |
| admin | `admin.replacing_a_key_or_re_enabling_a_connection_requires_role_reverification_calls_started_before_replac` | 키 교체·재활성화 후에는 역할 재검증이 필요합니다. 교체 전에 시작한 호출은 기존 키로 완료할 수 있습니다. 비활성화·삭제는 새 호출을 즉시 차단하고 이전 키로 시작한 호출에도 중단을 요청합니다. 제공자 처리 및 이미 발생한 비용은 취소되지 않을 수 있습니다. 모델 설정과 과거 기록은 보존합니다. | Replacing a key or re-enabling a connection requires role reverification. Calls started before replacement may finish with the previous key. Disabling or deleting blocks new calls immediately and requests cancellation of earlier calls. Provider processing and incurred costs may not be cancelled. Model settings and historical records are preserved. |
| admin | `admin.affected_items` | 영향 범위 | Affected items |
| admin | `admin.new_api_key` | 새 API 키 | New API key |
| admin | `admin.current_password` | 현재 비밀번호 | Current password |
| admin | `admin.submit_change` | 변경 제출 | Submit change |
| admin | `admin.non_generation_verification_and_catalog_refresh_completed` | 비생성 확인·목록 갱신을 완료했습니다. | Non-generation verification and catalog refresh completed. |
| admin | `admin.register_model` | 모델 등록 | Register model |
| admin | `admin.model_registered_role_testing_is_required_before_execution` | 모델 등록을 완료했습니다. 역할 시험 전에는 실행할 수 없습니다. | Model registered. Role testing is required before execution. |
| admin | `admin.new_models_are_registered_as_disabled_and_unverified_catalog_listing_does_not_replace_role_testing_m` | 새 모델은 비활성·미검증으로 등록합니다. 목록에 있어도 역할 시험은 별도로 필요합니다. 직접 입력 모델은 기능 정의가 필요할 수 있습니다. | New models are registered as disabled and unverified. Catalog listing does not replace role testing. Manually entered models may need a capability definition. |
| admin | `admin.model_id` | 모델 ID | Model ID |
| admin | `admin.display_name` | 표시명 | Display name |
| admin | `admin.model_enabled` | 모델 활성 | Model enabled |
| admin | `admin.enabling_requires_a_matching_capability_definition_and_catalog_plus_successful_current_role_verifica` | 모델 활성화에는 기능 정의·목록 일치와 현재 역할 검증 성공이 필요합니다. | Enabling requires a matching capability definition and catalog, plus successful current role verification. |
| admin | `admin.model_ids_cannot_change_after_registration_disabling_blocks_new_calls_only_the_server_validates_supp` | 모델 ID는 등록 후 변경할 수 없습니다. 모델 비활성화는 신규 호출만 막습니다. 지원 옵션 변경은 서버에서 조합과 범위를 검증합니다. | Model IDs cannot change after registration. Disabling blocks new calls only. The server validates supported option combinations and ranges. |
| admin | `admin.capability_definition_required_option_editing_and_role_tests_are_unavailable` | 기능 정의 필요: 옵션 편집과 역할 시험을 사용할 수 없습니다. | Capability definition required: option editing and role tests are unavailable. |
| admin | `admin.save_model_settings` | 모델 설정 저장 | Save model settings |
| admin | `admin.role_test` | 역할 시험 | Role test |
| admin | `admin.role_test_started_check_progress_or_cancel_explicitly` | 역할 시험을 시작했습니다. 진행 조회 또는 명시적 취소를 사용할 수 있습니다. | Role test started. Check progress or cancel explicitly. |
| admin | `admin.plain_text_and_streaming` | 일반 텍스트와 스트리밍 | Plain text and streaming |
| admin | `admin.verify_manual_coaching_and_automatic_no_intervention_as_separate_structured_responses` | 수동 코칭과 자동 미개입을 각각 단일 구조화 응답으로 확인 | Verify manual coaching and automatic no-intervention as separate structured responses |
| admin | `admin.v2_single_analysis_and_evidence_subset_synthesis_json` | v2 단일 분석과 근거 부분집합 종합 JSON | v2 single analysis and evidence-subset synthesis JSON |
| admin | `admin.up_to_2_generation_calls_maximum_output_per_call` | 최대 2회 생성 호출 · 각 호출 최대 출력 | Up to 2 generation calls · Maximum output per call |
| admin | `admin.tokens_no_automatic_retries` | 토큰 · 자동 재시도 0회 | tokens · No automatic retries |
| admin | `admin.charges_may_apply_tests_use_fixed_synthetic_inputs_success_does_not_guarantee_educational_quality_a_` | 유료 비용이 발생할 수 있습니다. 고정 합성 입력을 사용하며 시험 성공은 교육적 품질 보증이 아닙니다. 첫 단계 실패 시 다음 호출은 하지 않습니다. | Charges may apply. Tests use fixed synthetic inputs; success does not guarantee educational quality. A failed first step prevents the next call. |
| admin | `admin.each_administrator_can_run_one_test_batch_at_a_time_tests_do_not_queue_when_capacity_is_unavailable_` | 관리자당 시험은 한 묶음만 진행할 수 있습니다. 한도 부족 시 대기하지 않으므로 잠시 후 다시 시도하세요. 페이지를 닫아도 시험은 재시작하거나 중단되지 않습니다. 진행 조회와 시험 취소를 사용하세요. | Each administrator can run one test batch at a time. Tests do not queue when capacity is unavailable; try again shortly. Closing this page neither restarts nor stops the test. Use progress lookup or cancellation. |
| admin | `admin.start_test` | 시험 시작 | Start test |
| admin | `admin.test_status` | 시험 상태: | Test status: |
| admin | `admin.stopped` | 중단됨 | Stopped |
| admin | `admin.cancellation_requested_provider_processing_and_incurred_costs_may_not_be_cancelled` | 중단을 요청했습니다. 제공자 처리 및 이미 발생한 비용은 취소되지 않을 수 있습니다. | Cancellation requested. Provider processing and incurred costs may not be cancelled. |
| admin | `admin.the_admin_limit_must_be_below_the_total_limit_and_first_output_timeouts_must_not_exceed_total_timeou` | 관리자 한도는 전체 한도보다 작아야 하고 첫 본문 제한은 전체 시간 제한 이하여야 합니다. | The admin limit must be below the total limit, and first-output timeouts must not exceed total timeouts. |
| admin | `admin.defaults_apply_to_new_scenarios_only_existing_scenarios_and_sessions_remain_unchanged_unavailable_de` | 새 시나리오 작성용 기본값입니다. 기존 시나리오·세션은 변경하지 않습니다. 사용 불가 기본값을 자동으로 대체하지 않습니다. 한도·시간 변경은 이후 시작 호출에 적용합니다. | Defaults apply to new scenarios only. Existing scenarios and sessions remain unchanged. Unavailable defaults are not automatically replaced. Limit and timeout changes apply to future calls. |
| admin | `admin.default_authoring_model` | 작성 기본 모델 | Default authoring model |
| admin | `admin.total_call_limit` | 전체 호출 한도 | Total call limit |
| admin | `admin.openai_call_limit` | OpenAI 호출 한도 | OpenAI call limit |
| admin | `admin.claude_call_limit` | Claude 호출 한도 | Claude call limit |
| admin | `admin.gemini_call_limit` | Gemini 호출 한도 | Gemini call limit |
| admin | `admin.admin_task_limit` | 관리자 작업 한도 | Admin task limit |
| admin | `admin.concurrent_call_limits` | 동시 호출 한도 | Concurrent call limits |
| admin | `admin.connection_timeout_seconds` | 연결 제한(초) | Connection timeout (seconds) |
| admin | `admin.student_first_output_timeout_seconds` | 학생봇 첫 본문 제한(초) | Student first-output timeout (seconds) |
| admin | `admin.student_total_timeout_seconds` | 학생봇 전체 제한(초) | Student total timeout (seconds) |
| admin | `admin.mentor_first_output_timeout_seconds` | 멘토 첫 본문 제한(초) | Mentor first-output timeout (seconds) |
| admin | `admin.mentor_total_timeout_seconds` | 멘토 전체 제한(초) | Mentor total timeout (seconds) |
| admin | `admin.analysis_total_timeout_seconds` | 사후 분석 전체 제한(초) | Analysis total timeout (seconds) |
| admin | `admin.catalog_total_timeout_seconds` | 목록 전체 제한(초) | Catalog total timeout (seconds) |
| admin | `admin.timeouts` | 시간 제한 | Timeouts |
| admin | `admin.save_settings` | 설정 저장 | Save settings |
| admin | `admin.conversation_open` | 대화 진행 | Conversation open |
| admin | `admin.generating_responses` | 응답 생성 중 | Generating responses |
| admin | `admin.awaiting_student_response_adoption` | 학생 응답 채택 대기 | Awaiting student response adoption |
| admin | `admin.current_panel_retry_required` | 현재 패널 재시도 필요 | Current panel retry required |
| admin | `admin.analyzing` | 사후 분석 중 | Analyzing |
| admin | `admin.comparison_ended` | 비교 종료 | Comparison ended |
| admin | `admin.could_not_retrieve_comparison_records` | 비교 기록을 조회할 수 없습니다. | Could not retrieve comparison records. |
| admin | `admin.saved_comparison_state_confirmed` | 저장된 비교 상태를 확인했습니다. | Saved comparison state confirmed. |
| admin | `admin.could_not_find_or_open_this_comparison_conversation_your_input_is_preserved_choose_a_saved_scenario_` | 비교 대화를 찾거나 열 수 없습니다. 입력은 유지됩니다. 저장된 시나리오 선택 또는 상태 새로고침을 사용하세요. | Could not find or open this comparison conversation. Your input is preserved. Choose a saved scenario or refresh the state. |
| admin | `admin.another_request_was_processed_first_refresh_the_state` | 다른 요청이 먼저 처리되었습니다. 상태를 새로고침하세요. | Another request was processed first. Refresh the state. |
| admin | `admin.a_comparison_is_running` | 실행 중인 비교가 있습니다. | A comparison is running. |
| admin | `admin.adopt_a_completed_response_or_handle_the_automatic_check_first` | 완료 응답 채택 또는 자동 검사를 먼저 처리하세요. | Adopt a completed response or handle the automatic check first. |
| admin | `admin.check_the_model_connection_and_role_verification` | 모델·연결·역할 검증을 확인하세요. | Check the model, connection and role verification. |
| admin | `admin.the_execution_contract_changed_start_a_new_comparison` | 실행 계약이 변경되었습니다. 새 비교를 시작하세요. | The execution contract changed. Start a new comparison. |
| admin | `admin.check_the_input_limits_so_all_panels_can_process_the_common_input` | 모든 패널이 공통 입력을 처리할 수 있도록 입력 한도를 확인하세요. | Check the input limits so all panels can process the common input. |
| admin | `admin.the_attempt_changed_refresh_the_state` | 시도가 변경되었습니다. 상태를 새로고침하세요. | The attempt changed. Refresh the state. |
| admin | `admin.the_concurrent_call_limit_was_reached` | 동시 호출 한도에 도달했습니다. | The concurrent call limit was reached. |
| admin | `admin.could_not_find_or_open_this_comparison_conversation` | 비교 대화를 찾거나 열 수 없습니다. | Could not find or open this comparison conversation. |
| admin | `admin.could_not_process_the_request_your_input_is_preserved` | 요청을 처리하지 못했습니다. 입력을 보존했습니다. | Could not process the request. Your input is preserved. |
| admin | `admin.request_results_saved` | 요청 결과를 저장했습니다. | Request results saved. |
| admin | `admin.could_not_confirm_the_request_result_your_input_is_preserved_refresh_the_state_to_check_saved_result` | 요청 결과를 확인할 수 없습니다. 입력은 유지됩니다. 상태 새로고침으로 저장된 결과를 확인하세요. | Could not confirm the request result. Your input is preserved. Refresh the state to check saved results. |
| admin | `admin.recovery_lookup_ended_without_confirming_the_request_result_refresh_the_state_or_use_an_allowed_stop` | 복구 조회가 끝났습니다. 요청 결과는 확인되지 않았습니다. 상태 새로고침 또는 허용된 중지를 사용하세요. | Recovery lookup ended without confirming the request result. Refresh the state or use an allowed stop action. |
| admin | `admin.check_the_current_model_connection_role_verification_or_source_scenario` | 현재 모델·연결·역할 검증 또는 원본 시나리오를 확인하세요. | Check the current model, connection, role verification or source scenario. |
| admin | `admin.canonical_conversation_history` | 기준 대화 이력 | Canonical conversation history |
| admin | `admin.overall_status` | 전체 상태: | Overall status: |
| admin | `admin.expires` | 만료 | Expires |
| admin | `admin.other_panels_are_running_completed_responses_cannot_be_adopted_yet` | 다른 패널 실행 중 · 완료 응답도 채택할 수 없습니다. | Other panels are running · Completed responses cannot be adopted yet. |
| admin | `admin.an_automatic_mentor_check_is_pending_for_the_adopted_turn_complete_it_before_the_next_question_or_en` | 채택된 턴의 자동 멘토 검사가 대기 중입니다. 검사 후 다음 질문 또는 종료를 진행하세요. | An automatic mentor check is pending for the adopted turn. Complete it before the next question or ending. |
| admin | `admin.adopt_one_eligible_completed_response_to_update_the_canonical_conversation_history` | 완료 응답 중 서버가 허용한 응답 하나를 채택하면 다음 기준 대화 이력이 갱신됩니다. | Adopt one eligible completed response to update the canonical conversation history. |
| admin | `admin.response_adoption_is_currently_unavailable_refresh_the_state_to_check_eligibility` | 현재 응답 채택이 허용되지 않습니다. 상태 새로고침으로 조건을 확인하세요. | Response adoption is currently unavailable. Refresh the state to check eligibility. |
| admin | `admin.no_completed_response_is_available_to_adopt_check_retry_eligibility_for_the_current_panels` | 채택할 완료 응답이 없습니다. 현재 대상 패널의 재시도 조건을 확인하세요. | No completed response is available to adopt. Check retry eligibility for the current panels. |
| admin | `admin.teacher_prefix` | 교사: | Teacher: |
| admin | `admin.student_prefix` | 학생: | Student: |
| admin | `admin.send_question` | 질문 보내기 | Send question |
| admin | `admin.mentor_help` | 멘토 도움 | Mentor help |
| admin | `admin.pending_automatic_check` | 대기 중 자동 검사 | Pending automatic check |
| admin | `admin.end_comparison_and_analyze` | 비교 종료 및 분석 | End comparison and analyze |
| admin | `admin.refresh_state` | 상태 새로고침 | Refresh state |
| admin | `admin.results` | 결과 | Results |
| admin | `admin.panel` | 패널 | Panel |
| admin | `admin.adopt_student_response` | 학생 응답 채택 | Adopt student response |
| admin | `admin.retry_panel` | 패널 재시도 | Retry panel |
| admin | `admin.stop_panel` | 패널 중지 | Stop panel |
| admin | `admin.turn` | 턴 | Turn |
| admin | `admin.stop_all` | 전체 중지 | Stop all |
| admin | `admin.choose_a_saved_scenario_first` | 저장된 시나리오를 먼저 선택하세요. | Choose a saved scenario first. |
| admin | `admin.number_of_comparison_models` | 비교 모델 수 | Number of comparison models |
| admin | `admin.start_comparison` | 비교 시작 | Start comparison |
| admin | `admin.common_model` | 공통 모델 | Common model |
| admin | `admin.could_not_load_comparison_history_try_again` | 비교 이력을 불러오지 못했습니다. 다시 확인하세요. | Could not load comparison history. Try again. |
| admin | `admin.no_retained_comparison_conversations` | 보존 중인 비교 대화가 없습니다. | No retained comparison conversations. |
| admin | `admin.delete_comparison_record` | 비교 기록 삭제 | Delete comparison record |
| admin | `admin.delete_the_entire_comparison_record_the_cost_ledger_will_be_retained` | 전체 비교 기록을 삭제하시겠습니까? 비용 원장은 보존됩니다. | Delete the entire comparison record? The cost ledger will be retained. |
| admin | `admin.could_not_confirm_deletion_the_current_list_is_preserved` | 비교 기록 삭제 결과를 확인할 수 없습니다. 현재 목록은 유지됩니다. | Could not confirm deletion. The current list is preserved. |
| admin | `admin.could_not_delete_the_comparison_record_check_the_state_again` | 비교 기록을 삭제하지 못했습니다. 상태를 새로 확인하세요. | Could not delete the comparison record. Check the state again. |
| admin | `admin.next_20` | 다음 20개 | Next 20 |
| admin | `admin.comparison_history_list` | 비교 이력 목록 | Comparison history list |
| admin | `admin.comparison_history_details` | 비교 이력 상세 | Comparison history details |
| admin | `admin.your_comparisons_are_retained_for_30_days_after_creation_retrying_does_not_extend_their_expiry` | 내 비교 기록은 생성 후 30일 동안 보존합니다. 재시도해도 만료 시각은 연장되지 않습니다. | Your comparisons are retained for 30 days after creation. Retrying does not extend their expiry. |
| admin | `admin.could_not_find_or_open_the_comparison_viewing_retrying_and_copying_are_unavailable_after_30_days` | 비교를 찾거나 열 수 없습니다. 생성 후 30일이 지나면 조회·재시도·복사할 수 없습니다. | Could not find or open the comparison. Viewing, retrying and copying are unavailable after 30 days. |
| admin | `admin.my_comparison_history_newest_first_20_per_page` | 내 비교 이력 · 최신순 · 20개씩 | My comparison history · Newest first · 20 per page |
| admin | `admin.unsaved_edits_to_a_saved_scenario` | 저장 시나리오의 미저장 수정 | Unsaved edits to a saved scenario |
| admin | `admin.new_draft` | 새 초안 | New draft |
| admin | `admin.untitled_draft` | 제목 없는 초안 | Untitled draft |
| admin | `admin.details` | 상세 | Details |
| admin | `admin.no_retained_comparisons` | 보존 중인 비교가 없습니다. | No retained comparisons. |
| admin | `admin.created_timestamp_label` | 생성 | Created |
| admin | `admin.retained_for_30_days` | 30일 보존 | Retained for 30 days |
| admin | `admin.current_question` | 현재 질문: | Current question: |
| admin | `admin.common_input_hash` | 공통 입력 hash: | Common input hash: |
| admin | `admin.historical_panel` | 과거 패널 | Historical panel |
| admin | `admin.apply_this_model` | 이 모델 적용 | Apply this model |
| admin | `admin.open_source_scenario_editor` | 원본 시나리오 편집 폼 열기 | Open source scenario editor |
| admin | `admin.open_new_draft_with_these_conditions` | 이 조건으로 새 초안 열기 | Open new draft with these conditions |
| admin | `admin.delete_this_comparison_s_input_and_results_the_cost_ledger_will_be_retained` | 이 비교의 입력과 결과를 삭제하시겠습니까? 비용 원장은 보존됩니다. | Delete this comparison's input and results? The cost ledger will be retained. |
| admin | `admin.comparison_deleted_the_incurred_cost_ledger_is_retained` | 비교 기록을 삭제했습니다. 발생한 비용 원장은 보존됩니다. | Comparison deleted. The incurred-cost ledger is retained. |
| admin | `admin.could_not_delete_stop_the_running_comparison_before_deleting_again` | 삭제하지 못했습니다. 진행 중 비교는 중지 후 다시 삭제하세요. | Could not delete. Stop the running comparison before deleting again. |
| admin | `admin.running_comparisons_cannot_be_deleted_stop_the_comparison_and_check_again` | 진행 중 비교는 삭제할 수 없습니다. 중지 후 다시 확인하세요. | Running comparisons cannot be deleted. Stop the comparison and check again. |
| admin | `admin.could_not_load_comparison_details_try_again` | 비교 상세를 불러오지 못했습니다. 다시 확인하세요. | Could not load comparison details. Try again. |
| admin | `admin.waiting` | 대기 | Waiting |
| admin | `admin.processing` | 처리 중 | Processing |
| admin | `admin.streaming` | 스트리밍 | Streaming |
| admin | `admin.response_validation_failed` | 응답 검증에 실패했습니다. | Response validation failed. |
| admin | `admin.the_provider_request_limit_was_reached` | 제공자 요청 한도에 도달했습니다. | The provider request limit was reached. |
| admin | `admin.the_provider_refused_the_response` | 제공자가 응답을 거절했습니다. | The provider refused the response. |
| admin | `admin.the_response_timed_out` | 응답 제한 시간을 초과했습니다. | The response timed out. |
| admin | `admin.the_output_limit_was_reached` | 출력 한도에 도달했습니다. | The output limit was reached. |
| admin | `admin.this_run_was_cancelled_by_an_administrator` | 관리자가 취소한 실행입니다. | This run was cancelled by an administrator. |
| admin | `admin.permissions_or_ai_connection_model_availability_changed` | 권한 또는 AI 연결·모델 사용 가능 상태가 변경되었습니다. | Permissions or AI connection/model availability changed. |
| admin | `admin.saving_results_failed` | 결과 저장에 실패했습니다. | Saving results failed. |
| admin | `admin.execution_was_interrupted` | 실행이 중단되었습니다. | Execution was interrupted. |
| admin | `admin.the_provider_call_failed` | 제공자 호출에 실패했습니다. | The provider call failed. |
| admin | `admin.validated_partial_results_available` | 검증된 부분 결과 있음 | Validated partial results available |
| admin | `admin.no_validated_partial_results` | 검증된 부분 결과 없음 | No validated partial results |
| admin | `admin.model_unknown` | 모델 unknown | Model unknown |
| admin | `admin.status_unknown` | 상태 unknown | Status unknown |
| admin | `admin.provisional_response_incomplete_cannot_adopt` | 잠정 응답 · 미완료 · 채택 불가 | Provisional response · Incomplete · Cannot adopt |
| admin | `admin.no_mentor_intervention` | 멘토 미개입 | No mentor intervention |
| admin | `admin.mentor_coaching` | 멘토 코칭 | Mentor coaching |
| admin | `admin.started_timestamp_label` | 시작 | Started |
| admin | `admin.total_time` | 전체 시간 | Total time |
| admin | `admin.first_body_timestamp_label` | 첫 본문 | First output |
| admin | `admin.actual_input` | 실제 입력 | Actual input |
| admin | `admin.output` | 출력 | Output |
| admin | `admin.tokens_cost` | 토큰 · 비용 | tokens · Cost |
| admin | `admin.requested_model` | 요청 모델 | Requested model |
| admin | `admin.returned_model` | 반환 모델 | Returned model |
| admin | `admin.applied_options` | 적용 옵션: | Applied options: |
| admin | `admin.retries_incur_additional_costs_previous_attempts` | 재시도는 추가 비용이 발생합니다. 이전 시도 | Retries incur additional costs. Previous attempts |
| admin | `admin.cancellation_does_not_guarantee_refunds_of_incurred_costs` | 회 · 취소해도 발생한 비용은 환불을 보장하지 않습니다. | · Cancellation does not guarantee refunds of incurred costs. |
| admin | `admin.previous_attempt` | 이전 시도 | Previous attempt |
| admin | `admin.cost` | 비용 | Cost |
| admin | `admin.close_comparison` | 비교 닫기 | Close comparison |
| admin | `admin.common_input` | 공통 입력 | Common input |
| admin | `admin.comparison_role` | 비교 대상 | Comparison role |
| admin | `admin.add_completed_pair` | 완료 쌍 추가 | Add completed pair |
| admin | `admin.current_student_question` | 학생 현재 질문 | Current student question |
| admin | `admin.comparison_panels` | 비교 패널 | Comparison panels |
| admin | `admin.comparison_panel_results` | 비교 패널 결과 | Comparison panel results |
| admin | `admin.add_panel` | 패널 추가 | Add panel |
| admin | `admin.unconfigured_verify_the_target_role_in_ai_connections_and_models` | 미설정 · AI 연결·모델에서 대상 역할을 검증하세요. | Unconfigured · Verify the target role in AI connections and models. |
| admin | `admin.single_panel_test` | 단독 테스트 | Single-panel test |
| admin | `admin.panels_common_context_comparison` | 패널 · 동일 맥락 비교 | panels · Common-context comparison |
| admin | `admin.model_and_options` | 모델·옵션 | Model and options |
| admin | `admin.remove` | 제거 | Remove |
| admin | `admin.last_teacher_message_analysis_optional` | 마지막 교사 메시지 (분석, 선택) | Last teacher message (analysis, optional) |
| admin | `admin.the_full_canonical_conversation_history_is_used_classroom_recent_turn_limits_automatic_truncation_an` | 기준 대화 이력 전체를 사용합니다. 수업의 최근 N턴 제한·자동 절삭·분할은 적용하지 않습니다.  | The full canonical conversation history is used. Classroom recent-turn limits, automatic truncation and splitting do not apply.  |
| admin | `admin.mentor_off_enable_it_in_the_editor_before_running` | 멘토 off · 편집 폼에서 켠 뒤 실행하세요. | Mentor off · Enable it in the editor before running. |
| admin | `admin.intervention_judgment_test_classroom_start_turn_interval_and_count_limits_do_not_apply` | 개입 판단 시험: 수업의 시작 턴·간격·횟수 제한은 적용하지 않음 | Intervention judgment test: classroom start-turn, interval and count limits do not apply |
| admin | `admin.request_manual_coaching_for_the_last_completed_pair` | 마지막 완료 쌍에 수동 코칭을 요청합니다. | Request manual coaching for the last completed pair. |
| admin | `admin.analyze_the_full_dialogue_in_one_call_comparison_results_do_not_replace_educational_quality_approval` | 대화 전체를 단일 호출로 분석합니다. 비교 결과는 교육 품질 승인을 대신하지 않습니다. | Analyze the full dialogue in one call. Comparison results do not replace educational quality approval. |
| admin | `admin.compare_student_responses_to_the_current_question` | 현재 질문에 대한 학생 응답을 비교합니다. | Compare student responses to the current question. |
| admin | `admin.run_comparison` | 비교 실행 | Run comparison |
| admin | `admin.partial_subtotal_unknown_costs_present` | 부분 합계 (미확인 비용 포함) | Partial subtotal (unknown costs present) |
| admin | `admin.total_cost` | 비용 합계 | Total cost |
| admin | `admin.canonical_history_is_ready_for_the_next_comparison_the_original_comparison_is_preserved` | 다음 비교의 기준 대화 이력을 준비했습니다. 원본 비교는 유지됩니다. | Canonical history is ready for the next comparison. The original comparison is preserved. |
| admin | `admin.open_the_source_editor_or_a_new_draft_with_these_conditions_to_apply_the_model` | 현재 원본 편집 폼 또는 이 조건의 새 초안을 열어 적용하세요. | Open the source editor or a new draft with these conditions to apply the model. |
| admin | `admin.the_comparison_is_running_apply_after_it_ends` | 비교가 진행 중입니다. 종료 후 적용하세요. | The comparison is running. Apply after it ends. |
| admin | `admin.could_not_find_or_open_the_comparison_or_panel_check_the_history_again` | 비교 또는 패널을 찾거나 열 수 없습니다. 이력을 다시 확인하세요. | Could not find or open the comparison or panel. Check the history again. |
| admin | `admin.check_current_role_verification_connection_and_options_before_applying` | 현재 역할 검증·연결·옵션을 확인한 뒤 적용하세요. | Check current role verification, connection and options before applying. |
| admin | `admin.could_not_apply_the_model_check_again` | 모델을 적용하지 못했습니다. 다시 확인하세요. | Could not apply the model. Check again. |
| admin | `admin.model_and_options_were_applied_to_the_unsaved_form_save_or_publish_separately` | 대상 역할의 모델·옵션을 미저장 폼에 적용했습니다. 별도 저장 또는 게시가 필요합니다. | Model and options were applied to the unsaved form. Save or publish separately. |
| admin | `admin.could_not_find_or_open_the_comparison_records_are_retained_for_30_days_after_creation` | 비교를 찾거나 열 수 없습니다. 보존기간은 생성 후 30일입니다. | Could not find or open the comparison. Records are retained for 30 days after creation. |
| admin | `admin.a_comparison_is_running_or_an_attempt_changed_check_the_state_again` | 진행 중 비교 또는 변경된 시도가 있습니다. 상태를 새로 확인하세요. | A comparison is running or an attempt changed. Check the state again. |
| admin | `admin.could_not_reserve_capacity_for_all_panels_try_again_shortly` | 전체 패널 슬롯을 확보하지 못했습니다. 잠시 후 다시 실행하세요. | Could not reserve capacity for all panels. Try again shortly. |
| admin | `admin.check_ai_connection_and_model_settings` | AI 연결·모델 설정을 확인하세요. | Check AI connection and model settings. |
| admin | `admin.check_common_input_and_model_options` | 공통 입력·모델 옵션을 확인하세요. | Check common input and model options. |
| admin | `admin.the_input_is_too_large` | 입력이 너무 큽니다. | The input is too large. |
| admin | `admin.source_scenario_not_found_compare_from_a_new_draft` | 원본 시나리오를 찾을 수 없습니다. 새 초안에서 비교하세요. | Source scenario not found. Compare from a new draft. |
| admin | `admin.could_not_process_the_comparison_request_your_input_is_preserved` | 비교 요청을 처리하지 못했습니다. 입력을 보존했습니다. | Could not process the comparison request. Your input is preserved. |
| admin | `admin.enable_the_mentor_and_add_a_completed_pair` | 멘토를 켜고 완료 쌍을 추가하세요. | Enable the mentor and add a completed pair. |
| admin | `admin.enter_a_teacher_message` | 교사 메시지를 입력하세요. | Enter a teacher message. |
| admin | `admin.checking_common_input_for_all_panels` | 전체 패널의 공통 입력을 확인 중입니다. | Checking common input for all panels. |
| admin | `admin.comparison_ended_check_each_panel_s_results` | 비교가 종료되었습니다. 패널별 결과를 확인하세요. | Comparison ended. Check each panel's results. |
| admin | `admin.in_progress_refresh_the_state_or_stop` | 진행 중 · 상태 새로고침 또는 중지를 사용할 수 있습니다. | In progress · Refresh the state or stop. |
| admin | `admin.connection_interrupted_refresh_the_state_to_check_saved_results_your_input_is_preserved` | 연결이 중단되었습니다. 상태 새로고침으로 저장된 결과를 확인하세요. 입력을 보존했습니다. | Connection interrupted. Refresh the state to check saved results. Your input is preserved. |
| admin | `admin.frozen_conditions_from_a_saved_comparison` | 저장된 비교의 고정 조건 | Frozen conditions from a saved comparison |
| admin | `admin.saved_comparison_results_recovered` | 저장된 비교 결과를 복구했습니다. | Saved comparison results recovered. |
| admin | `admin.recovery_lookup_ended_refresh_the_state_or_stop` | 복구 조회가 끝났습니다. 상태 새로고침 또는 중지를 사용하세요. | Recovery lookup ended. Refresh the state or stop. |
| admin | `admin.could_not_confirm_the_state_check_again` | 상태를 확인하지 못했습니다. 다시 확인하세요. | Could not confirm the state. Check again. |
| admin | `admin.could_not_confirm_the_stop_state_refresh_the_state` | 중지 상태를 확인하지 못했습니다. 상태를 새로 확인하세요. | Could not confirm the stop state. Refresh the state. |
| admin | `admin.reset_comparison_input` | 비교 입력 초기화 | Reset comparison input |
| admin | `admin.comparison_input_reset_saved_comparison_records_are_preserved` | 편집 중 비교 입력을 초기화했습니다. 저장된 비교 기록은 유지됩니다. | Comparison input reset. Saved comparison records are preserved. |
| admin | `admin.unsaved_draft` | 미저장 초안 | Unsaved draft |
| admin | `admin.new_draft_no_source` | 새 작성 · 원본 없음 | New draft · No source |
| admin | `admin.source_scenario` | 원본 | Source |
| admin | `admin.save_failed` | 저장 실패 | Save failed |
| admin | `admin.added` | 등록되었습니다. | Added. |
| admin | `admin.save_result_unknown_input_preserved` | 저장 결과를 확인할 수 없습니다. 현재 입력은 유지됩니다. | Could not confirm the save result. Your input is preserved. |
| admin | `admin.updated` | 수정되었습니다. | Updated. |
| admin | `admin.delete_this_item` | 항목을 삭제하시겠습니까? | Delete this item? |
| admin | `admin.contributor_deletion_failed` | 삭제 실패 | Deletion failed |
| admin | `admin.deleted` | 삭제되었습니다. | Deleted. |
| admin | `admin.could_not_confirm_deletion_check_the_list_again` | 삭제 결과를 확인할 수 없습니다. 목록을 새로 확인하세요. | Could not confirm deletion. Check the list again. |
| admin | `admin.loading_analysis_details` | 분석 상세를 불러오는 중... | Loading analysis details... |
| admin | `admin.could_not_load_analysis_details_close_and_try_again` | 분석 상세를 불러올 수 없습니다. 닫은 뒤 다시 시도하세요. | Could not load analysis details. Close and try again. |
| admin | `admin.lookup_failed` | 조회 실패 | Lookup failed |
| admin | `admin.no_completed_sessions_message` | 완료된 세션이 없습니다. | No completed sessions. |
| admin | `admin.date_and_time` | 일시 | Date and time |
| admin | `admin.messages` | 메시지 | Messages |
| admin | `admin.download` | 다운로드 | Download |
| admin | `admin.individual_sessions` | 개별 세션 목록 | Individual sessions |
| admin | `admin.could_not_load_the_session_list` | 세션 목록을 불러올 수 없습니다. | Could not load the session list. |
| admin | `admin.user_created` | 사용자가 생성되었습니다! | User created! |
| admin | `admin.user_updated` | 사용자가 업데이트되었습니다! | User updated! |
| admin | `admin.delete_this_user` | 사용자를 삭제하시겠습니까? | Delete this user? |
| admin | `admin.user_deleted` | 사용자가 삭제되었습니다! | User deleted! |
| admin | `admin.an_error_occurred_while_processing_the_file` | 파일 처리 중 오류가 발생했습니다. | An error occurred while processing the file. |
| admin | `admin.could_not_connect_to_the_server` | 서버 연결에 실패했습니다. | Could not connect to the server. |
| admin | `admin.bulk_add_users_preview` | 일괄 사용자 추가 — 미리보기 | Bulk add users — Preview |
| admin | `admin.valid` | 정상 | Valid |
| admin | `admin.errors` | 에러 | Errors |
| admin | `admin.no_valid_users_to_register` | 등록할 유효한 사용자가 없습니다. | No valid users to register. |
| admin | `admin.could_not_confirm_registration_check_the_user_list_before_trying_again` | 등록 결과를 확인할 수 없습니다. 사용자 목록을 확인한 뒤 다시 시도하세요. | Could not confirm registration. Check the user list before trying again. |
| admin | `admin.bulk_add_users_results` | 일괄 사용자 추가 — 결과 | Bulk add users — Results |
| admin | `admin.copied` | 복사했습니다. | Copied. |
| admin | `admin.could_not_copy_download_as_csv` | 복사할 수 없습니다. CSV로 내려받으세요. | Could not copy. Download as CSV. |
| admin | `admin.group_created` | 그룹이 생성되었습니다! | Group created! |
| admin | `admin.group_updated` | 그룹이 업데이트되었습니다! | Group updated! |
| admin | `admin.delete_this_group` | 그룹을 삭제하시겠습니까? | Delete this group? |
| admin | `admin.group_deleted` | 그룹이 삭제되었습니다! | Group deleted! |
| admin | `admin.list_page` | 현재 {page} 페이지 · 20개씩 · 총 {total}개 | Page {page} · 20 per page · {total} total |
| admin | `admin.session_page` | 총 {total}개 중 {page}/{pages} 페이지 | Page {page}/{pages} · {total} total |
| admin | `admin.people_count` | {count}명 | {count} |
| admin | `admin.student_label` | 학생 | Student |
| admin | `admin.group_has_members` | "{name}" 그룹에 {count}명의 멤버가 있어 삭제할 수 없습니다. 먼저 멤버를 다른 그룹으로 이동하세요. | Cannot delete group "{name}" with {count} members. Move its members to another group first. |
| admin | `admin.admin_required` | 관리자 권한이 필요합니다. | Admin role required |
| admin | `admin.session_deleted` | 이미 삭제된 세션입니다. | Session already deleted |
| admin | `admin.end_before_analysis` | 분석을 보려면 먼저 세션을 종료하세요. | Session must be ended before viewing analysis |
| admin | `admin.user_missing` | 사용자를 찾을 수 없습니다. | User not found |
| admin | `admin.sessions_missing` | 세션이 없습니다. | No sessions found |
| admin | `admin.session_selection_required` | 세션을 선택하세요. | No session_ids provided |
| admin | `admin.valid_sessions_missing` | 유효한 세션이 없습니다. | No valid sessions |
| admin | `admin.completed_sessions_missing` | 완료된 세션이 없습니다. | No completed sessions found |
| admin | `admin.active_download` | 진행 중인 세션은 다운로드할 수 없습니다. | Cannot download active session |
| admin | `admin.active_session` | 세션 {session_id}이 진행 중입니다. 종료된 세션만 가능합니다. | Session {session_id} is still active. Only ended allowed. |
| admin | `admin.contributor_not_found` | 등록자를 찾을 수 없습니다. | Contributor not found. |
| admin | `admin.group_name_already_exists` | 이미 존재하는 그룹 이름입니다. | Group name already exists. |
| admin | `admin.group_not_found` | 그룹을 찾을 수 없습니다. | Group not found. |
| admin | `admin.cannot_delete_group_with_members` | 이 그룹에 {member_count}명의 멤버가 있어 삭제할 수 없습니다. 먼저 멤버를 다른 그룹으로 이동하세요. | Cannot delete this group with {member_count} members. Move its members to another group first. |
| admin | `admin.user_ids_may_contain_only_letters_numbers_and_underscores` | 사용자 ID는 영문, 숫자, 언더스코어만 사용 가능합니다. | User IDs may contain only letters, numbers and underscores. |
| admin | `admin.user_id_already_exists` | 이미 존재하는 사용자 ID입니다. | User ID already exists. |
| admin | `admin.invalid_role` | 유효하지 않은 역할입니다. | Invalid role. |
| admin | `admin.user_not_found` | 사용자를 찾을 수 없습니다. | User not found. |
| admin | `admin.you_cannot_delete_yourself` | 자기 자신은 삭제할 수 없습니다. | You cannot delete yourself. |
| admin | `admin.users_with_active_sessions_cannot_be_deleted_end_their_sessions_first` | 활성 세션이 있는 사용자는 삭제할 수 없습니다. 먼저 세션을 종료해주세요. | Users with active sessions cannot be deleted. End their sessions first. |
| admin | `admin.users_with_model_test_records_cannot_be_deleted` | 모델 시험 기록이 있는 사용자는 삭제할 수 없습니다. | Users with model test records cannot be deleted. |
| admin | `admin.the_file_exceeds_1mb` | 파일 크기가 1MB를 초과합니다. | The file exceeds 1MB. |
| admin | `admin.unrecognized_file_encoding_use_utf_8_or_euc_kr` | 파일 인코딩을 인식할 수 없습니다. UTF-8 또는 EUC-KR을 사용해주세요. | Unrecognized file encoding. Use UTF-8 or EUC-KR. |
| admin | `admin.could_not_read_csv_headers` | CSV 헤더를 읽을 수 없습니다. | Could not read CSV headers. |
| admin | `admin.required_columns_missing` | 필수 컬럼이 누락되었습니다: {missing_columns}. (발견된 컬럼: {found_columns}) | Required columns are missing: {missing_columns}. (Found columns: {found_columns}) |
| admin | `admin.the_csv_file_is_empty` | CSV 파일이 비어 있습니다. | The CSV file is empty. |
| admin | `admin.upload_user_limit` | 최대 {limit}명까지 업로드할 수 있습니다. 현재 {count}명 | Upload up to {limit} users. Current count: {count} |
| admin | `admin.user_ids_must_contain_3_to_50_characters` | 사용자 ID는 3자 이상 50자 이하여야 합니다. | User IDs must contain 3 to 50 characters. |
| admin | `admin.nicknames_must_contain_2_to_30_characters` | 닉네임은 2자 이상 30자 이하여야 합니다. | Nicknames must contain 2 to 30 characters. |
| admin | `admin.invalid_role_value` | 유효하지 않은 역할: {role} | Invalid role: {role} |
| admin | `admin.bulk_user_id_exists` | 이미 존재하는 사용자 ID | User ID already exists |
| admin | `admin.duplicate_user_id_in_csv` | CSV 내 중복된 사용자 ID | Duplicate user ID in CSV |
| admin | `admin.group_not_found_value` | 존재하지 않는 그룹: {group_name} | Group not found: {group_name} |
| admin | `admin.invalid_user_id_format` | 유효하지 않은 사용자 ID 형식 | Invalid user ID format |
| admin | `admin.bulk_invalid_role` | 유효하지 않은 역할 | Invalid role |
| admin | `admin.an_error_occurred_while_registering_the_user` | 사용자 등록 중 오류가 발생했습니다. | An error occurred while registering the user. |
| admin | `admin.registered_model_description` | 등록 모델: {display_name} ({model_id}) | Registered model: {display_name} ({model_id}) |
| admin | `admin.scenario_model_usage` | 시나리오 #{scenario_id} {title} · {role} ({model_id}) · {state} · {execution} | Scenario #{scenario_id} {title} · {role} ({model_id}) · {state} · {execution} |
| admin | `admin.role_default_authoring_model` | {role} 작성 기본 모델: {model_name} | {role} default authoring model: {model_name} |
| admin | `admin.mentor_off_excluded_from_execution` | 멘토 off · 실행 제외 | Mentor off · Excluded from execution |
| admin | `admin.used_for_new_lessons` | 새 수업 실행 참조 | Used for new lessons |
| admin | `admin.excluded_from_new_lessons` | 새 수업 실행 제외 | Excluded from new lessons |
| admin | `admin.maximum_output_tokens` | 최대 출력 토큰 | Maximum output tokens |
| admin | `admin.reasoning_effort` | 추론 수준 | Reasoning effort |
| admin | `admin.temperature` | 다양성 | Temperature |
| admin | `admin.unspecified_uses_the_provider_default` | 지정하지 않음은 제공자 기본값을 사용합니다. | Unspecified uses the provider default. |
| admin | `admin.the_official_default_reasoning_effort_needs_verification_unspecified_omits_the_value_and_does_not_su` | 공식 기본 추론 수준은 확인 필요입니다. 지정하지 않음은 값을 보내지 않으며, 다른 모델의 기본값으로 대체하지 않습니다. | The official default reasoning effort needs verification. Unspecified omits the value and does not substitute another model's default. |
| admin | `admin.temperature_is_supported_only_with_reasoning_effort_none` | temperature는 추론 수준 none일 때만 지원합니다. | Temperature is supported only with reasoning effort none. |
| admin | `admin.temperature_is_not_supported` | temperature는 지원하지 않습니다. | Temperature is not supported. |
| admin | `admin.the_enabled_manual_budget_is_deprecated_budget_must_be_at_least_1024_and_below_the_maximum_output_to` | enabled 수동 budget은 deprecated입니다. budget은 최대 출력 토큰보다 작고 1024 이상이어야 합니다. thinking 사용 시 temperature는 1만 허용합니다. | The enabled manual budget is deprecated. Budget must be at least 1024 and below the maximum output tokens. Thinking permits temperature 1 only. |
| admin | `admin.temperature_permits_the_default_1_only_manual_thinking_budgets_are_unsupported` | temperature는 기본값 1만 허용합니다. 수동 thinking budget은 지원하지 않습니다. | Temperature permits the default 1 only. Manual thinking budgets are unsupported. |
| admin | `admin.between_tools_supports_low_medium_and_high_effort_only_disabled_is_unsupported` | between_tools는 low·medium·high effort에서만 지원합니다. disabled는 지원하지 않습니다. | between_tools supports low, medium and high effort only. disabled is unsupported. |
| admin | `admin.disabled_supports_low_medium_and_high_effort_only` | disabled는 low·medium·high effort에서만 지원합니다. | disabled supports low, medium and high effort only. |
| admin | `admin.adaptive_thinking_is_always_enabled_disabled_is_unsupported` | adaptive thinking은 항상 활성입니다. disabled는 지원하지 않습니다. | Adaptive thinking is always enabled. disabled is unsupported. |
| admin | `admin.claude_thinking_mode` | Claude thinking 방식 | Claude thinking mode |
| admin | `admin.claude_thinking_token_budget` | Claude thinking 토큰 예산 | Claude thinking token budget |
| admin | `admin.claude_effort` | Claude effort | Claude effort |
| admin | `admin.thinking_token_budget_1_auto_0_off` | 사고 토큰 예산 (-1 자동, 0 끄기) | Thinking token budget (-1 auto, 0 off) |
| admin | `admin.thinking_level` | 사고 수준 | Thinking level |
| admin | `admin.unspecified_uses_dynamic_thinking_1_is_automatic_0_is_off_and_1_24576_is_a_numeric_budget_level_is_u` | 지정하지 않음은 동적 thinking입니다. -1 자동, 0 끄기, 1–24576 숫자 budget을 구분합니다. level은 지원하지 않습니다. | Unspecified uses dynamic thinking. -1 is automatic, 0 is off and 1–24576 is a numeric budget. level is unsupported. |
| admin | `admin.unspecified_uses_the_provider_default_level_level_and_budget_cannot_be_combined_this_app_edits_gemin` | 지정하지 않음은 제공자 기본 level을 사용합니다. level과 budget은 함께 지정할 수 없습니다. 이 앱의 Gemini 3 옵션 편집은 level을 사용합니다. 기존 budget 저장 값은 보존하며 명시적 수정이 필요합니다. | Unspecified uses the provider default level. level and budget cannot be combined. This app edits Gemini 3 options using level. Existing saved budgets are preserved and require explicit editing. |
| admin | `admin.minimal_does_not_guarantee_that_thinking_is_fully_disabled` | minimal은 사고의 완전한 비활성화를 보장하지 않습니다. | minimal does not guarantee that thinking is fully disabled. |
| admin | `admin.minimal_is_unsupported` | minimal은 지원하지 않습니다. | minimal is unsupported. |
| admin | `admin.active_call_count` | 활성 호출: {count}건 | Active calls: {count} |
| admin | `admin.invalid_date` | {field} 날짜 형식이 유효하지 않습니다: '{value}' | Invalid {field} format: '{value}' |
| admin | `admin.configuration` | 실행 설정 | Configuration |
| admin | `admin.rubric_id` | ID | ID |
| admin | `admin.rubric_field` | 분류 {index} {label} | Classification {index} {label} |
| admin | `admin.role_model` | {role} 모델 | {role} model |
| admin | `admin.review_input` | {field} 입력값을 확인하세요. | Check input for {field}. |
| admin | `admin.review_invalid_source` | {field} 원문을 확인하세요. | Check the original value for {field}. |
| admin | `admin.review_privacy` | 공개 학생 소개는 비워 두었습니다. 내부 원문과 공개 범위를 검토하세요. | The public student profile was left empty. Review the internal original and public visibility. |
| admin | `admin.review_missing_template` | 참조 템플릿이 없습니다. 역할 지시를 보완하세요. | Referenced template missing. Complete the role instructions. |
| admin | `admin.review_unsupported_template` | 알 수 없거나 손상된 템플릿을 원문 archive와 비교하여 보완하세요. | Repair the unknown or damaged template using the original archive. |
| admin | `admin.review_rolling` | 기존 고정 주기 제한에서 최근 완료 10턴의 rolling 제한으로 바뀝니다. | The fixed-bucket limit changes to a rolling window of the last 10 completed turns. |
| admin | `admin.review_sensitivity` | 기존 민감도는 실행에서 제거됩니다. 자동 개입 조건을 직접 작성하세요. | Legacy sensitivity is removed from execution. Write the automatic intervention condition explicitly. |
| admin | `admin.review_temperature` | 기존 chat_temperature는 적용되지 않았으므로 새 옵션에 넣지 않았습니다. | Legacy chat_temperature was not applied and was not added to the new options. |
| admin | `admin.review_missing_rubric` | 이전 분석 틀이 없습니다. 분류 기준을 보완하세요. | Legacy analysis framework missing. Complete the rubric. |
| admin | `admin.review_model` | {field}의 유효 모델·연결·역할 검증과 옵션을 확인하세요. | Check the available model, connection, role verification and options for {field}. |
| admin | `admin.review_subcall` | 이전 하위 호출과 제안 역할의 모델·옵션이 다릅니다. 하나의 역할 설정으로 통합됩니다. | Legacy subcalls and the proposed role use different models or options. They are consolidated into one role configuration. |
| admin | `admin.review_oversize` | 변환 설정이 UTF-8 입력 한도를 넘었습니다. 전체 원문 archive를 확인하여 초안을 보완하세요. | Converted settings exceed the UTF-8 input limit. Complete the draft using the full original archive. |
| admin | `admin.review_rubric_id` | {field} ID 형식을 확인하세요. | Check the rubric ID format for {field}. |
| admin | `admin.review_duplicate_id` | 분류 ID는 중복 없이 작성하세요. | Use unique rubric IDs. |
| admin | `admin.review_duplicate_name` | 분류 이름은 중복 없이 작성하세요. | Use unique rubric names. |
| admin | `admin.review_common_model` | 모든 활성 역할에 같은 모델을 선택하세요. | Select the same model for all active roles. |
| admin | `admin.review_conversion` | 기존 시나리오 변환이 필요합니다. | Legacy scenario conversion is required. |
| admin | `admin.review_groups` | 배정 그룹을 확인하세요. | Check the assigned groups. |
| admin | `admin.review_acknowledgement` | 변환 경고 검토 확인이 필요합니다. | Acknowledge the conversion warnings. |
| admin | `admin.comparison_input_error` | 공통 입력과 선택 모델 옵션을 확인하세요. | Check the common input and selected model options. |
| admin | `admin.input_format` | 입력 형식과 한도를 확인하세요. | Check the input format and limits. |
| admin | `admin.request_size` | 요청은 1 MiB 이하여야 합니다. | Requests must not exceed 1 MiB. |
| admin | `admin.authentication_required` | 로그인이 필요합니다. | Authentication required |
| admin | `admin.access_required` | 관리자 권한이 필요합니다. | Admin access required |
| admin | `admin.trigger_auto` | 자동 | Automatic |
| admin | `admin.trigger_manual` | 수동 | Manual |
| admin | `admin.decision_admitted` | 실행 승인 | Admitted |
| admin | `admin.decision_before_start` | 검사 시작 전 | Before check start |
| admin | `admin.decision_interval` | 검사 간격 대기 | Waiting for check interval |
| admin | `admin.decision_cap` | 개입 상한 도달 | Intervention cap reached |
| admin | `admin.selected_count` | {count}개 선택됨 | {count} selected |
| admin | `admin.sessions_loading` | 분석 결과 로딩 중... | Loading analysis results... |
| admin | `admin.downloading` | 다운로드 중... | Downloading... |
| admin | `admin.download_failed` | 다운로드 실패 | Download failed |
| admin | `admin.selected_download_failed` | 선택한 세션을 다운로드하지 못했습니다. 권한과 세션 상태를 확인한 뒤 다시 시도하세요. | Could not download the selected sessions. Check permissions and session status, then try again. |
| admin | `admin.session_action_unknown` | 작업 결과를 확인할 수 없습니다. 목록을 새로 확인하세요. | Could not confirm the task result. Check the list again. |
| admin | `admin.session_action_failed` | 세션 작업을 완료하지 못했습니다. 권한과 세션 상태를 확인하세요. | Could not complete the session action. Check permissions and session status. |
| admin | `admin.review_field_required` | {field} 보완 필요 | Complete {field}. |
