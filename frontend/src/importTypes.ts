export const IMPORT_HEADERS=["subject_code","grade_number","topic_code","subtopic_code","title","statement","task_type","answer_format","difficulty","source","primary_skill_code","primary_skill_weight","additional_skills"] as const;
export type ImportHeader=typeof IMPORT_HEADERS[number];
export type LocalIssueCode="unsupported_file_type"|"file_too_large"|"invalid_utf8"|"missing_tasks_sheet"|"duplicate_tasks_sheet"|"missing_header"|"unknown_header"|"duplicate_header"|"empty_file"|"too_many_rows"|"invalid_grade"|"invalid_enum"|"invalid_weight"|"invalid_skills_syntax"|"duplicate_skill"|"catalog_reference_not_found"|"catalog_hierarchy_mismatch";
export type ImportIssue={severity:"error"|"warning";code:LocalIssueCode|string;field?:string;message:string;row_number?:number;source:"local"|"server"};
export type ParsedImportRow={row_number:number;values:Record<ImportHeader,string>;issues:ImportIssue[]};
export type ParsedImport={format:"csv"|"xlsx";rows:ParsedImportRow[];issues:ImportIssue[]};
export const MAX_FILE_SIZE=5*1024*1024,MAX_ROWS=500;
