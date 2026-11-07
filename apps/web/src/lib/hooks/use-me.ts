"use client";

import { useQuery } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api/client";
import type { MeResponse } from "@/lib/api/types";

export function useMe() {
  return useQuery<MeResponse, ApiError>({
    queryKey: ["auth", "me"],
    queryFn: api.getMe,
    retry: false,
    staleTime: 30_000,
  });
}
