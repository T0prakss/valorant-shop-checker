import { useEffect, useRef, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import * as api from '../api/client';

export default function LoginPage() {
  const { state, dispatch } = useAuth();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(false);
  const [polling, setPolling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  if (state.status === 'authenticated') {
    return <Navigate to="/shop" replace />;
  }

  // Clean up polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  async function handleLogin() {
    setLoading(true);
    setError(null);

    try {
      const { auth_url } = await api.startAuth();

      // Open Riot login in a new tab
      window.open(auth_url, '_blank', 'noopener');

      // Start polling for auth completion
      setPolling(true);
      setLoading(false);

      pollRef.current = setInterval(async () => {
        try {
          const res = await api.pollAuth();
          if (res.status === 'success' && res.puuid) {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setPolling(false);
            dispatch({ type: 'LOGIN_SUCCESS', puuid: res.puuid });
            navigate('/shop');
          } else if (res.status === 'error') {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setPolling(false);
            setError(res.error ?? 'Authentication failed');
          }
        } catch {
          // Network error — keep polling
        }
      }, 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start login');
      setLoading(false);
    }
  }

  function handleCancel() {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
    setPolling(false);
    setError(null);
  }

  return (
    <div className="relative flex min-h-svh items-center justify-center overflow-hidden bg-bg-primary px-4">
      {/* Angular geometric background accents */}
      <div className="pointer-events-none absolute inset-0" aria-hidden="true">
        <div className="absolute -left-20 top-1/4 h-px w-80 rotate-[35deg] bg-gradient-to-r from-transparent via-accent-red/20 to-transparent" />
        <div className="absolute -right-10 top-1/3 h-px w-96 -rotate-[25deg] bg-gradient-to-r from-transparent via-accent-red/15 to-transparent" />
        <div className="absolute bottom-1/4 left-1/4 h-px w-64 rotate-[55deg] bg-gradient-to-r from-transparent via-accent-teal/10 to-transparent" />
        <div className="absolute -right-16 bottom-1/3 h-px w-72 rotate-[40deg] bg-gradient-to-r from-transparent via-accent-red/10 to-transparent" />
        <div className="absolute left-1/3 top-16 h-px w-48 -rotate-[15deg] bg-gradient-to-r from-transparent via-border to-transparent" />
        <div className="absolute bottom-20 right-1/4 h-px w-56 rotate-[65deg] bg-gradient-to-r from-transparent via-border to-transparent" />
      </div>

      <div className="relative w-full max-w-md">
        <div className="rounded-lg border border-border bg-bg-secondary p-8 shadow-2xl">
          <h1
            className="mb-1 text-center text-4xl tracking-wider text-text-primary"
            style={{ fontFamily: "'Oswald', sans-serif", fontWeight: 700 }}
          >
            VALORANT <span className="text-accent-red">SHOP</span>
          </h1>
          <p className="mb-8 text-center text-sm text-text-secondary">
            Check your daily store without launching the game
          </p>

          {!polling ? (
            <>
              <button
                onClick={handleLogin}
                disabled={loading}
                className="flex w-full items-center justify-center gap-2 rounded bg-accent-red py-3 text-sm font-bold uppercase tracking-widest text-white transition-all hover:brightness-110 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-60"
                style={{ fontFamily: "'Oswald', sans-serif" }}
              >
                {loading && <Spinner />}
                SIGN IN WITH RIOT
              </button>

              {error && (
                <p className="mt-4 text-center text-sm text-accent-red">{error}</p>
              )}
            </>
          ) : (
            <div className="animate-slide-in text-center">
              <div className="mb-4 flex items-center justify-center gap-3 text-accent-teal">
                <Spinner />
                <span className="text-sm font-medium">Waiting for login...</span>
              </div>
              <p className="mb-6 text-sm text-text-secondary">
                Complete the login in the browser tab that just opened.
                <br />
                This page will update automatically.
              </p>
              <button
                onClick={handleCancel}
                className="text-xs uppercase tracking-widest text-text-secondary transition-colors hover:text-text-primary"
                style={{ fontFamily: "'Oswald', sans-serif" }}
              >
                Cancel
              </button>
            </div>
          )}
        </div>

        <p className="mt-6 text-center text-xs leading-relaxed text-text-secondary/70">
          You'll be redirected to Riot's official login page.
          <br />
          Your credentials are entered directly on Riot's servers —
          this app never sees your password.
        </p>
        <p className="mt-4 text-center text-[10px] leading-relaxed text-text-secondary/50">
          This application is not endorsed by Riot Games and does not reflect the
          views or opinions of Riot Games or anyone officially involved in producing
          or managing Riot Games properties. Riot Games and all associated properties
          are trademarks or registered trademarks of Riot Games, Inc.
        </p>
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <svg className="h-5 w-5 animate-spin" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}
