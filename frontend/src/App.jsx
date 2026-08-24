import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Layout from './layouts/Layout';
import Dashboard from './pages/Dashboard';
import Cases from './pages/Cases';
import CaseDetail from './pages/CaseDetail';
import AgentMonitor from './pages/AgentMonitor';
import Analytics from './pages/Analytics';
import Policy from './pages/Policy';

function App() {
  return (
    <Router>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/cases" element={<Cases />} />
          <Route path="/cases/:id" element={<CaseDetail />} />
          <Route path="/agent" element={<AgentMonitor />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/policy" element={<Policy />} />
        </Route>
      </Routes>
    </Router>
  );
}

export default App;
