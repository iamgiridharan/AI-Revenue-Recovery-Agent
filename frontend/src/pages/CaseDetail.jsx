import { useParams } from 'react-router-dom';

export default function CaseDetail() {
  const { id } = useParams();

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-800 mb-4">Case Details</h1>
      <p className="text-gray-600 mb-6">
        Detailed view of case <span className="font-mono text-blue-600">#{id}</span>
      </p>
      <div className="bg-white rounded-lg shadow p-8 text-center">
        <p className="text-gray-500">Case details will be implemented in Phase 2.</p>
      </div>
    </div>
  );
}
