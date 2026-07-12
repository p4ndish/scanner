import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from './lib/auth.jsx'
import Layout from './components/Layout'
import Login from './pages/Login'
import Register from './pages/Register'
import Dashboard from './pages/Dashboard'
import NewScan from './pages/NewScan'
import ScanList from './pages/ScanList'
import ScanDetail from './pages/ScanDetail'
import Results from './pages/Results'
import Verification from './pages/Verification'

function PrivateRoute({ children }) {
  const { token } = useAuth()
  return token ? children : <Navigate to="/login" replace />
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route
        path="/*"
        element={
          <PrivateRoute>
            <Layout>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/scans/new" element={<NewScan />} />
                <Route path="/scans" element={<ScanList />} />
                <Route path="/scans/:id" element={<ScanDetail />} />
                <Route path="/results" element={<Results />} />
                <Route path="/verification" element={<Verification />} />
              </Routes>
            </Layout>
          </PrivateRoute>
        }
      />
    </Routes>
  )
}

export default App
