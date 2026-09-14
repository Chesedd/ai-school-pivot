import {beforeEach, describe, expect, it, vi} from "vitest";
import {createUser, patchUser} from "./adminApi";
import {request} from "./api";

vi.mock("./api", () => ({request: vi.fn()}));
const mockedRequest = vi.mocked(request);

describe("admin user API", () => {
  beforeEach(() => mockedRequest.mockReset());

  it("creates a user with structured names and generated-password mode only", () => {
    createUser({first_name: "Иван", last_name: "Иванов", roles: ["student"], password: {mode: "generated"}});
    const body = JSON.parse(String(mockedRequest.mock.calls[0][1]?.body));
    expect(body).toEqual({first_name: "Иван", last_name: "Иванов", roles: ["student"], password: {mode: "generated"}});
    expect(body).not.toHaveProperty("login"); expect(body).not.toHaveProperty("display_name"); expect(body).not.toHaveProperty("student_id");
  });

  it("sends a provided password and structured name updates", () => {
    createUser({first_name: "Анна", last_name: "Петрова", roles: [], password: {mode: "provided", value: "safe-password"}});
    expect(JSON.parse(String(mockedRequest.mock.calls[0][1]?.body)).password).toEqual({mode: "provided", value: "safe-password"});
    patchUser("user/1", {first_name: "Мария", last_name: "Петрова"});
    expect(mockedRequest.mock.calls[1][0]).toBe("/api/admin/users/user%2F1");
    expect(JSON.parse(String(mockedRequest.mock.calls[1][1]?.body))).toEqual({first_name: "Мария", last_name: "Петрова"});
  });
});
