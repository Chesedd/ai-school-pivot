// @vitest-environment jsdom
import {cleanup,render,screen,waitFor} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {afterEach,describe,expect,it,vi} from "vitest";
import {AttachmentPanel} from "./AttachmentPanel";
import * as api from "./api";

afterEach(()=>{cleanup();vi.restoreAllMocks()});
const image={id:"image-1",filename:"diagram.png",mime_type:"image/png" as const,size_bytes:8,created_at:"2026-08-21T00:00:00Z",role:"statement",url:"/api/attachments/image-1/content"};

describe("AttachmentPanel",()=>{
 it("uploads and displays an allowed task image",async()=>{vi.spyOn(api,"listTaskAttachments").mockResolvedValue({items:[]});const upload=vi.spyOn(api,"uploadTaskAttachment").mockResolvedValue(image);render(<AttachmentPanel versionId="version-1" editable/>);await userEvent.upload(screen.getByLabelText("Добавить изображение"),new File([new Uint8Array([137,80,78,71,13,10,26,10])],"diagram.png",{type:"image/png"}));expect(await screen.findByAltText("diagram.png")).toBeTruthy();expect(upload).toHaveBeenCalledWith("version-1","statement",expect.objectContaining({name:"diagram.png"}))});
 it("restores saved images after reload",async()=>{vi.spyOn(api,"listTaskAttachments").mockResolvedValue({items:[image]});const first=render(<AttachmentPanel versionId="version-1" editable={false}/>);expect(await screen.findByAltText("diagram.png")).toBeTruthy();first.unmount();render(<AttachmentPanel versionId="version-1" editable={false}/>);await waitFor(()=>expect(screen.getByAltText("diagram.png")).toBeTruthy());expect(api.listTaskAttachments).toHaveBeenCalledTimes(2)});
});
