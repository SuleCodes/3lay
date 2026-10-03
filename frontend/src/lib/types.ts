export interface User {
  id: string;
  email: string;
  created_at: string;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface VerifyResponse {
  user: User;
  created_api_key: string | null;
}

export interface ApiKeyCreatedResponse {
  api_key: ApiKey;
  key: string;
}
