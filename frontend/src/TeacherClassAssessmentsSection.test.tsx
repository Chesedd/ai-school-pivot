import {describe,expect,it} from "vitest";
import {assignmentDisplayStatus} from "./TeacherClassAssessmentsSection";
const base={status:"open" as const,start_at:"2026-09-07T10:00:00Z",due_at:"2026-09-07T12:00:00Z"};
describe("class assignment display status",()=>{it("derives states without adding server statuses",()=>{expect(assignmentDisplayStatus(base,Date.parse("2026-09-07T09:00:00Z"))).toBe("Запланирована");expect(assignmentDisplayStatus(base,Date.parse("2026-09-07T11:00:00Z"))).toBe("Идёт");expect(assignmentDisplayStatus(base,Date.parse("2026-09-07T13:00:00Z"))).toBe("Срок истёк");expect(assignmentDisplayStatus({...base,status:"closed"})).toBe("Закрыта")})});
