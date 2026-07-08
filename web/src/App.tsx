import { useAuth } from "./context/AuthContext";
import Login from "./pages/Login";
import Studio from "./pages/Studio";

export default function App() {
  const { user, loading } = useAuth();
  if (loading) return <div className="screen center"><span className="muted">Загрузка…</span></div>;
  return user ? <Studio /> : <Login />;
}
