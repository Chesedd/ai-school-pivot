import {describe,expect,it} from "vitest";
import {createXlsxTemplate} from "./ImportPage";
import {parseAdditionalSkills,parseImportFile,parseTags} from "./importParser";
import {IMPORT_HEADERS,MAX_FILE_SIZE} from "./importTypes";
const csv=(line:string,name="tasks.csv")=>new File(["\uFEFF"+IMPORT_HEADERS.join(",")+"\n"+line],name,{type:"text/csv"});
const valid='math,7,motion,,"Title, quoted","Line 1\nLine 2",problem,number,25,,speed,1.0,';
describe("import parsers",()=>{
 it("delegates CSV quoting and logical rows to PapaParse",async()=>{const p=await parseImportFile(csv(valid));expect(p.issues).toEqual([]);expect(p.rows[0].values.title).toBe("Title, quoted");expect(p.rows[0].values.statement).toBe("Line 1\nLine 2");expect(p.rows[0].row_number).toBe(2)});
 it("supports semicolon CSV",async()=>{const p=await parseImportFile(new File([IMPORT_HEADERS.join(";")+"\n"+valid.replaceAll(",",";")],"x.csv"));expect(p.rows).toHaveLength(1)});
 it("rejects invalid UTF-8",async()=>expect((await parseImportFile(new File([new Uint8Array([0xff])],"x.csv"))).issues[0].code).toBe("invalid_utf8"));
 it("rejects wrong headers",async()=>expect((await parseImportFile(new File(["wrong\nvalue"],"x.csv"))).issues.some(x=>x.code==="missing_header")).toBe(true));
 it("rejects 501 rows",async()=>{const data=IMPORT_HEADERS.join(",")+"\n"+Array(501).fill(valid).join("\n");expect((await parseImportFile(new File([data],"x.csv"))).issues[0].code).toBe("too_many_rows")});
 it("checks file type and size before reading",async()=>{expect((await parseImportFile(new File(["x"],"x.xls"))).issues[0].code).toBe("unsupported_file_type");expect((await parseImportFile(new File([new Uint8Array(MAX_FILE_SIZE+1)],"x.xlsx"))).issues[0].code).toBe("file_too_large")});
 it("keeps old files without tags compatible and splits semicolon tag names",async()=>{const old=IMPORT_HEADERS.filter(h=>h!=="tags");const p=await parseImportFile(new File([old.join(",")+"\n"+valid],"old.csv"));expect(p.issues).toEqual([]);expect(p.rows[0].values.tags).toBe("");expect(parseTags(" ОГЭ; С параметром ",2).result).toEqual(["ОГЭ","С параметром"])});
 it("rejects empty tag fragments and more than eight names",()=>{expect(parseTags("ОГЭ;;ЕГЭ",2).issues[0].code).toBe("tag_name_invalid");expect(parseTags(Array(9).fill("tag").join(";"),2).issues[0].code).toBe("tag_limit_exceeded")});
 it("validates additional skills",()=>{expect(parseAdditionalSkills("loops:.3|loops:.2","speed",2).issues[0].code).toBe("duplicate_skill");expect(parseAdditionalSkills("bad","speed",2).issues[0].code).toBe("invalid_skills_syntax")});
 it("creates and reads a formula-free empty Tasks template",async()=>{const blob=await createXlsxTemplate();const file=new File([blob],"template.xlsx");const p=await parseImportFile(file);expect(p.issues[0].code).toBe("empty_file");const bytes=new TextDecoder("latin1").decode(await blob.arrayBuffer());expect(bytes).not.toContain("<f>")});
});
