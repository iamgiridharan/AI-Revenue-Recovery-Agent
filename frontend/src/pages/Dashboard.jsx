export default function Dashboard() {
  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-800 mb-4">Dashboard</h1>
      <p className="text-gray-600 mb-6">
        Overview of revenue recovery metrics and system status.
      </p>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white rounded-lg shadow p-6">
          <h3 className="text-sm font-medium text-gray-500">Total Revenue at Risk</h3>
          <p className="text-2xl font-bold text-red-600 mt-2">$0.00</p>
          <p className="text-xs text-gray-400 mt-1">Phase 2 will populate this</p>
        </div>
        <div className="bg-white rounded-lg shadow p-6">
          <h3 className="text-sm font-medium text-gray-500">Recovered Revenue</h3>
          <p className="text-2xl font-bold text-green-600 mt-2">$0.00</p>
          <p className="text-xs text-gray-400 mt-1">Phase 2 will populate this</p>
        </div>
        <div className="bg-white rounded-lg shadow p-6">
          <h3 className="text-sm font-medium text-gray-500">Active Cases</h3>
          <p className="text-2xl font-bold text-blue-600 mt-2">0</p>
          <p className="text-xs text-gray-400 mt-1">Phase 2 will populate this</p>
        </div>
      </div>
    </div>
  );
}
