"use client";
import * as React from "react";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, clearSessionClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

type AuthProfile = components["schemas"]["AuthProfile"];
const AuthContext = React.createContext<{user: AuthProfile | undefined; isPending: boolean; error: Error | null; refresh: () => Promise<unknown>; logout: () => Promise<void>} | null>(null);
function AuthProvider({children}: {children: React.ReactNode}) {
  const cache = useQueryClient();
  const hadUser = React.useRef(false);
  const query = useQuery({queryKey:["auth"],queryFn:async () => {
    try { return await api<AuthProfile>("/auth/me"); }
    catch (error) { if (error instanceof ApiError && error.status === 401) return null; throw error; }
  },retry:false,staleTime:30_000});
  React.useEffect(() => {
    if (query.data) hadUser.current = true;
    else if (query.data === null && hadUser.current) {
      hadUser.current = false;
      clearSessionClient();
      cache.clear();
      window.location.assign("/login");
    }
  }, [query.data, cache]);
  React.useEffect(()=>{
    const clear=()=>cache.clear();
    window.addEventListener("wm:session-ended",clear);
    return ()=>window.removeEventListener("wm:session-ended",clear);
  },[cache]);
  async function logout() {
    await api<void>("/auth/logout",{method:"POST"});
    clearSessionClient(); cache.clear(); window.location.assign("/login");
  }
  return <AuthContext.Provider value={{user:query.data ?? undefined,isPending:query.isPending,error:query.error,refresh:query.refetch,logout}}>{children}</AuthContext.Provider>;
}
export function useAuth(){const context=React.useContext(AuthContext);if(!context)throw new Error("AuthProvider missing");return context;}
export function Providers({children}: {children:React.ReactNode}) {
  const [client]=React.useState(()=>new QueryClient({defaultOptions:{queries:{retry:1,refetchOnWindowFocus:false}}}));
  return <QueryClientProvider client={client}><AuthProvider>{children}</AuthProvider></QueryClientProvider>;
}
