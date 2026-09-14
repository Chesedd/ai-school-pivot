import {request} from "./api";

export type Role = "admin" | "teacher" | "student";
export type PasswordRequest = {mode: "generated"} | {mode: "provided"; value: string};
export type CreateUserRequest = {
  first_name: string;
  last_name: string;
  roles: Role[];
  password: PasswordRequest;
};
export type AdminUser = {
  user_id: string;
  login: string;
  display_name: string;
  first_name: string | null;
  last_name: string | null;
  is_active: boolean;
  roles: Role[];
  // Kept for response compatibility. Account administration must not depend on this link.
  student_id: string | null;
  created_at: string;
  updated_at: string;
};
export type CreatedAdminUser = AdminUser & {generated_password?: string};

export const listUsers = () => request<{items: AdminUser[]; total: number; offset: number; limit: number}>("/api/admin/users");
export const createUser = (value: CreateUserRequest) => request<CreatedAdminUser>("/api/admin/users", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(value)});
export const getUser = (id: string) => request<AdminUser>(`/api/admin/users/${encodeURIComponent(id)}`);
export const patchUser = (id: string, value: {first_name?: string; last_name?: string; is_active?: boolean}) => request<AdminUser>(`/api/admin/users/${encodeURIComponent(id)}`, {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify(value)});
export const replaceRoles = (id: string, roles: Role[]) => request<AdminUser>(`/api/admin/users/${encodeURIComponent(id)}/roles`, {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({roles})});
export const resetPassword = (id: string, new_password: string) => request<void>(`/api/admin/users/${encodeURIComponent(id)}/password-reset`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({new_password})});
