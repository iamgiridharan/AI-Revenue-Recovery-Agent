export default function Cases() {
  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-800 mb-4">Revenue Risk Cases</h1>
      <p className="text-gray-600 mb-6">
        View and manage identified payment failures and their recovery status.
      </p>
      <div className="bg-white rounded-lg shadow p-8 text-center">
        <p className="text-gray-500">No cases yet.</p>
        <p className="text-xs text-gray-400 mt-2">
          Cases will appear here once payment failure detection is implemented in Phase 2.
        </p>
      </div>
    </div>
  );
}
