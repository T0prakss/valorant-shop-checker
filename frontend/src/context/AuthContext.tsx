import {
  createContext,
  useEffect,
  useReducer,
  type Dispatch,
  type ReactNode,
} from 'react';
import type { AuthAction, AuthState } from '../types';
import * as api from '../api/client';

const initialState: AuthState = {
  status: 'idle',
  sessionValid: false,
  puuid: null,
  error: null,
};

function authReducer(state: AuthState, action: AuthAction): AuthState {
  switch (action.type) {
    case 'LOGIN_START':
      return { ...state, status: 'loading', error: null };
    case 'LOGIN_SUCCESS':
      return {
        status: 'authenticated',
        sessionValid: true,
        puuid: action.puuid,
        error: null,
      };
    case 'LOGIN_ERROR':
      return { ...state, status: 'error', error: action.error };
    case 'LOGOUT':
      return { ...initialState };
    case 'SESSION_RESTORED':
      return {
        status: 'authenticated',
        sessionValid: true,
        puuid: action.puuid,
        error: null,
      };
  }
}

export const AuthContext = createContext<{
  state: AuthState;
  dispatch: Dispatch<AuthAction>;
}>({
  state: initialState,
  dispatch: () => undefined,
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(authReducer, initialState);

  useEffect(() => {
    api.checkSession().then((res) => {
      if (res.valid && res.puuid) {
        dispatch({ type: 'SESSION_RESTORED', puuid: res.puuid });
      }
    }).catch(() => {
      // No valid session, stay idle
    });
  }, []);

  return (
    <AuthContext.Provider value={{ state, dispatch }}>
      {children}
    </AuthContext.Provider>
  );
}
