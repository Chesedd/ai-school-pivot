import Papa from "papaparse";
import {IMPORT_HEADERS,MAX_FILE_SIZE,MAX_ROWS,type ImportHeader,type ImportIssue,type ParsedImport,type ParsedImportRow} from "./importTypes";

const issue=(code:string,message:string,row_number?:number,field?:string):ImportIssue=>({severity:"error",code,message,row_number,field,source:"local"});
const blank=(row:unknown[])=>row.every(x=>String(x??"").trim()==="");
function table(format:"csv"|"xlsx",data:unknown[][],rowNumbers?:number[]):ParsedImport{
 const raw=(data[0]??[]).map(x=>String(x??"").trim());if(raw[0])raw[0]=raw[0].replace(/^\uFEFF/,"");
 const issues:ImportIssue[]=[];const seen=new Set<string>();
 raw.forEach(h=>{if(seen.has(h))issues.push(issue("duplicate_header",`Заголовок «${h}» повторяется.`,1,h));seen.add(h);if(h&&!IMPORT_HEADERS.includes(h as ImportHeader))issues.push(issue("unknown_header",`Неизвестный заголовок «${h}».`,1,h))});
 IMPORT_HEADERS.forEach(h=>{if(!raw.includes(h))issues.push(issue("missing_header",`Отсутствует заголовок «${h}».`,1,h))});
 if(issues.length)return {format,rows:[],issues};
 const rows:ParsedImportRow[]=[];
 data.slice(1).forEach((cells,index)=>{if(blank(cells))return;const values=Object.fromEntries(IMPORT_HEADERS.map(h=>[h,String(cells[raw.indexOf(h)]??"").trim()])) as Record<ImportHeader,string>;rows.push({row_number:rowNumbers?.[index]??index+2,values,issues:[]})});
 if(!rows.length)issues.push(issue("empty_file","Файл не содержит строк данных."));
 if(rows.length>MAX_ROWS)issues.push(issue("too_many_rows",`Допустимо не более ${MAX_ROWS} непустых строк.`));
 return {format,rows,issues};
}
export async function parseImportFile(file:File):Promise<ParsedImport>{
 const ext=file.name.toLowerCase().match(/\.[^.]+$/)?.[0];
 if(ext!==".csv"&&ext!==".xlsx")return {format:"csv",rows:[],issues:[issue("unsupported_file_type","Поддерживаются только файлы .csv и .xlsx.")]};
 if(file.size>MAX_FILE_SIZE)return {format:ext.slice(1) as "csv"|"xlsx",rows:[],issues:[issue("file_too_large","Размер файла превышает 5 МиБ.")]};
 if(ext===".csv"){
  let text:string;try{text=new TextDecoder("utf-8",{fatal:true}).decode(await file.arrayBuffer())}catch{return {format:"csv",rows:[],issues:[issue("invalid_utf8","CSV должен быть корректным UTF-8.")]}}
  const parsed=Papa.parse<string[]>(text,{delimiter:"",skipEmptyLines:false});
  if(parsed.meta.delimiter&& !([",",";"].includes(parsed.meta.delimiter)))return {format:"csv",rows:[],issues:[issue("unknown_header","CSV должен использовать запятую или точку с запятой.")]};
  return table("csv",parsed.data);
 }
 const {default:readXlsxFile}=await import("read-excel-file/browser");
 const sheets=await readXlsxFile(file);const tasks=sheets.filter(x=>x.sheet==="Tasks");if(tasks.length!==1)return {format:"xlsx",rows:[],issues:[issue(tasks.length?"duplicate_tasks_sheet":"missing_tasks_sheet",tasks.length?"Лист Tasks должен встречаться ровно один раз.":"В книге отсутствует лист Tasks.")]};
 return table("xlsx",tasks[0].data);
}
export function parseAdditionalSkills(value:string,primary:string,row:number){const result:{code:string;weight:string}[]=[],issues:ImportIssue[]=[];if(!value.trim())return {result,issues};const codes=new Set([primary]);for(const part of value.split("|")){const bits=part.split(":");if(bits.length!==2||!bits[0].trim()||!bits[1].trim()){issues.push(issue("invalid_skills_syntax","Используйте формат code:weight|code:weight.",row,"additional_skills"));continue}const code=bits[0].trim(),weight=bits[1].trim();if(codes.has(code))issues.push(issue("duplicate_skill",`Навык «${code}» повторяется.`,row,"additional_skills"));codes.add(code);if(!validWeight(weight))issues.push(issue("invalid_weight",`Некорректный вес навыка «${code}».`,row,"additional_skills"));result.push({code,weight})}return {result,issues}}
export const validWeight=(value:string)=>/^(?:(?:0?\.\d+)|1(?:\.0+)?|0)$/.test(value)&&Number(value)>0&&Number(value)<=1;
