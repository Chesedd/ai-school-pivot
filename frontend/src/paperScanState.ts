import {ApiError} from "./api";
import {errorMessage} from "./errors";
import type {ScanArtifact, ScanBatch, ScanPage} from "./paperScanApi";

// Accepted signatures/MIME types in application/input_artifacts.py. Size/capacity
// limits are deployment settings and deliberately remain server-authoritative.
export const scanAccept = "application/pdf,image/jpeg,image/png,image/webp";
export const batchLabels: Record<string, string> = {
  draft: "Ожидает файлов", uploading: "Загрузка файлов", extracting: "Обработка файлов",
  matching: "Сопоставление", matching_review_required: "Нужно проверить сопоставление",
  grouping_confirmed: "Работы сгруппированы", ready_for_checking: "Готово к проверке",
  checking: "Проверка работ", checking_completed: "Работы проверены", completed: "Завершено",
  processing_failed: "Обработка остановлена", cancelled: "Отменено",
};
export const artifactLabels: Record<string, string> = {
  pending: "Ожидает обработки", running: "Обрабатывается", completed: "Обработан",
  failed_retryable: "Не удалось обработать — можно повторить", failed_terminal: "Не удалось обработать",
};
const messages: Record<string, string> = {
  matching_already_running: "Сопоставление уже выполняется. Данные обновлены.",
  matching_claim_lost: "Сопоставление продолжает выполняться. Данные обновлены.",
  matching_commit_outcome_unknown: "Уточняем результат сопоставления. Обновите данные.",
  timeout: "Не удалось завершить сопоставление. Данные обновлены.",
  rate_limit: "Сопоставление временно недоступно. Данные обновлены.",
  connection_error: "Не удалось завершить сопоставление. Данные обновлены.",
  provider_unavailable: "Сопоставление временно недоступно. Данные обновлены.",
  unexpected_internal_error: "Не удалось завершить сопоставление. Данные обновлены.",
  matching_persistence_failed: "Не удалось сохранить сопоставление. Данные обновлены.",
  page_content_integrity_failure: "Изображение страницы повреждено. Вернитесь к файлам.",
  scan_batch_not_ready_for_matching: "Сопоставление недоступно в текущем состоянии партии.",
  scan_pages_incomplete: "Сначала завершите обработку всех страниц.",
  scan_pages_failed: "Есть необработанные страницы. Вернитесь к файлам.",
  missing_derived_render: "Изображения страниц не готовы. Вернитесь к файлам.",
  scan_matching_provider_not_configured: "Сопоставление сейчас недоступно. Обратитесь к администратору.",
  grouping_revision_conflict: "Распределение изменилось. Мы загрузили актуальные данные.",
  grouping_based_on_stale_matching: "Результат сопоставления изменился. Мы загрузили актуальные данные.",
  grouping_immutable: "Распределение уже подтверждено или этап изменился. Данные обновлены.",
  grouping_initialization_not_allowed: "Создание распределения недоступно на этом этапе.",
  grouping_confirmation_not_allowed: "Этап партии изменился. Данные обновлены.",
  grouping_page_not_in_batch: "Страница не принадлежит этой партии. Данные обновлены.",
  grouping_participant_not_in_assignment: "Ученик не участвует в этом назначении.",
  participant_not_in_assignment: "Ученик не участвует в этом назначении.",
  grouping_variant_required: "Для ученика не назначен вариант. Обратитесь к администратору.",
  unmatched_pages_block_confirmation: "Распределите все страницы перед подтверждением.",
  grouping_requires_group: "Распределите страницы хотя бы в одну работу.",
  grouping_pages_not_ready: "Некоторые страницы ещё не готовы.",
  grouping_duplicate_page: "Страница повторяется в работе. Данные обновлены.",
  duplicate_page_assignment: "Страница повторяется в распределении. Данные обновлены.",
  duplicate_participant: "У ученика должна быть одна работа в партии.",
  page_coverage_mismatch: "Состав страниц изменился. Мы загрузили актуальные данные.",
  grouping_page_order_mismatch: "Состав работы изменился. Мы загрузили актуальные данные.",
  grouping_page_order_invalid: "Проверьте порядок страниц в работе.",
  paper_submission_conflict: "Работа ученика уже создана. Данные обновлены.",
  scan_batch_paper_limit_exceeded: "В партии слишком много работ.",
  paper_submission_page_limit_exceeded: "В работе слишком много страниц.",
  scan_page_content_purged: "Страница больше не доступна.",
  unsupported_artifact_type: "Выберите PDF, JPEG, PNG или WebP.",
  invalid_artifact_signature: "Содержимое файла не соответствует его формату. Выберите PDF, JPEG, PNG или WebP.",
  artifact_too_small: "Файл пустой.", artifact_too_large: "Файл слишком большой.",
  scan_artifact_too_large: "Файл слишком большой.",
  scan_batch_artifact_limit_exceeded: "Достигнут лимит файлов в этой партии.",
  scan_batch_byte_limit_exceeded: "Достигнут лимит общего размера файлов в этой партии.",
  scan_artifact_page_limit_exceeded: "В файле слишком много страниц.",
  scan_batch_page_limit_exceeded: "Достигнут лимит страниц в этой партии.",
  image_too_large: "Размер изображения превышает допустимый.",
  normalized_page_too_large: "Изображение страницы превышает допустимый размер.",
  pdf_page_too_large: "Страница PDF превышает допустимый размер.",
  pdf_page_limit_exceeded: "В PDF слишком много страниц.",
  artifact_attachment_not_allowed: "Эта партия больше не принимает файлы. Данные обновлены.",
  extraction_not_allowed: "Обработка недоступна в текущем состоянии партии. Данные обновлены.",
  duplicate_upload_position: "Другой учитель уже загрузил файл. Данные обновлены; проверьте список перед повторной загрузкой.",
  idempotency_conflict: "Запрос создания конфликтует с предыдущим. Обновите список партий.",
  scan_batch_has_no_artifacts: "Сначала загрузите файлы.",
  page_render_failed: "Не удалось обработать файл. Повторите обработку.",
  artifact_integrity_failed: "Исходный файл повреждён и не может быть обработан.",
  invalid_pdf: "Не удалось прочитать PDF.", invalid_image: "Не удалось прочитать изображение.",
  encrypted_pdf: "PDF защищён паролем. Используйте файл без защиты.",
  invalid_instruction: "Укажите инструкцию к работам (до 10 000 символов).",
  invalid_upload_form: "Не удалось отправить файл. Выберите файлы заново.",
  scan_content_missing: "Исходный файл недоступен. Метаданные сохранены.",
  scan_content_unavailable: "Не удалось получить исходный файл. Попробуйте скачать его позже.",
  scan_artifact_content_purged: "Исходный файл больше не хранится",
  assignment_has_no_participants: "В назначении нет участников.",
};
export const scanFailureMessage = (code: string | null) => code && messages[code] || "Не удалось обработать файл.";
export function scanErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Войдите, чтобы продолжить.";
    if (error.status === 403) return "Работа не найдена или больше вам недоступна.";
    if (messages[error.code]) return messages[error.code];
    if (error.status === 404) return "Работа не найдена или больше вам недоступна.";
    if (error.status >= 500) return "Ошибка сервера. Обновите данные и повторите действие.";
    if (error.status === 409) return "Состояние изменилось. Обновите данные и повторите действие.";
  }
  return errorMessage(error, "Не удалось выполнить действие. Проверьте соединение и обновите данные.");
}
// Never render arbitrary server messages, provider errors, or raw response details.
export function matchingErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 422 && !messages[error.code]) return "Не удалось выполнить действие. Обновите данные и проверьте распределение.";
  if (error instanceof ApiError) return scanErrorMessage(error);
  return "Не удалось выполнить действие. Проверьте соединение и обновите данные.";
}
export type ScanSnapshot = {batch: ScanBatch; artifacts: ScanArtifact[]; pages: ScanPage[]};
export const acceptsUpload = (batch: ScanBatch) => ["draft", "uploading"].includes(batch.status);
export const progressed = (batch: ScanBatch) => ["matching", "matching_review_required", "grouping_confirmed", "ready_for_checking", "checking", "checking_completed", "completed"].includes(batch.status);
export function intakeReady(data: ScanSnapshot): boolean {
  return data.batch.status === "extracting" && data.artifacts.length > 0 && data.pages.length > 0 &&
    data.artifacts.every(a => a.extraction_status === "completed" && a.page_count === data.pages.filter(p => p.source_artifact_id === a.id).length) &&
    data.pages.every(p => p.status === "ready" && !!p.content_url && !!p.content_fingerprint);
}
export function needsPolling(data: ScanSnapshot): boolean {
  return data.batch.status === "extracting" && data.artifacts.some(a => ["pending", "running"].includes(a.extraction_status));
}
export function canExtract(data: ScanSnapshot): boolean {
  return data.artifacts.length > 0 && (data.batch.status === "uploading" ||
    data.batch.status === "extracting" && data.artifacts.some(a => ["pending", "running", "failed_retryable"].includes(a.extraction_status)));
}
