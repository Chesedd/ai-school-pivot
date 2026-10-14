import type {PublicationPaper, PublicationHistory} from "./paperPublicationApi";

export function publicationState(p: PublicationPaper) {
  if (p.active_publication_id) {
    if (p.latest_approved_revision_id && p.latest_approved_revision_id !== p.active_publication_approved_review_revision_id) return "new";
    return "published";
  }
  return p.latest_approved_revision_id ? "ready" : "review";
}
export const publicationLabels = {
  review: "Нет подтверждённой проверки", ready: "Готово к публикации",
  published: "Опубликовано", new: "Опубликована предыдущая версия",
};
export function publishLabel(p: PublicationPaper, history?: PublicationHistory[]) {
  if (publicationState(p) === "new") return "Опубликовать новую версию";
  if (!p.active_publication_id && history?.some(x => x.is_withdrawn)) return "Опубликовать снова";
  return "Опубликовать";
}
