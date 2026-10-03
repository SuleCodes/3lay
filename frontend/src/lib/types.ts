export interface User {
  id: string;
  email: string;
  created_at: string;
  // Both null until the user has chosen a username during onboarding.
  username: string | null;
  forwarding_address: string | null;
}

export interface DeleteAccountResponse {
  message: string;
  deleted_stored_items: number;
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
  // True until the user has chosen a username (/onboarding), which is also
  // what creates their first API key.
  needs_username: boolean;
}

export interface UsernameAvailability {
  username: string;
  available: boolean;
  reason: string | null;
  forwarding_address: string | null;
}

export interface UsernameClaimedResponse {
  user: User;
  api_key: ApiKeyCreatedResponse;
}

export interface ApiKeyCreatedResponse {
  api_key: ApiKey;
  key: string;
}
