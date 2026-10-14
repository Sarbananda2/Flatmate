import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { ToastProvider } from './components/Toast'
import AuditPage from './pages/Audit'
import DashboardPage from './pages/Dashboard'
import DefinitionsPage from './pages/Definitions'
import ElectricityPage from './pages/services/Electricity'
import HousingHubPage from './pages/housing/HousingHub'
import ExpensesPage from './pages/Expenses'
import PaymentsPage from './pages/Payments'
import ReconciliationPage from './pages/Reconciliation'
import RentPage from './pages/services/Rent'
import ReportsPage from './pages/Reports'
import ResidentDetailPage from './pages/ResidentDetail'
import ResidentsPage from './pages/Residents'
import ServicesHubPage from './pages/services/ServicesHub'
import SetupPage from './pages/Setup'
import SetupWizardPage from './pages/SetupWizard'
import StatementDetailPage from './pages/StatementDetail'
import StatementsPage from './pages/Statements'
import TransactionsPage from './pages/Transactions'

export default function App() {
  return (
    <ToastProvider>
      <Layout>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/residents" element={<ResidentsPage />} />
          <Route path="/residents/:residentId" element={<ResidentDetailPage />} />
          <Route path="/definitions" element={<DefinitionsPage />} />
          <Route path="/housing" element={<HousingHubPage />} />
          <Route path="/services" element={<ServicesHubPage />} />
          <Route path="/services/rent" element={<RentPage />} />
          <Route path="/services/electricity" element={<ElectricityPage />} />
          {/* The addresses these pages had before they moved under /services. */}
          <Route path="/rent" element={<Navigate to="/services/rent" replace />} />
          {/* Housing pages that used to stand alone, kept for old links and bookmarks. */}
          <Route path="/services/rent/config" element={<Navigate to="/housing?tab=lease" replace />} />
          <Route path="/rent/config" element={<Navigate to="/housing?tab=lease" replace />} />
          <Route path="/services/deposits" element={<Navigate to="/housing?tab=deposits" replace />} />
          <Route path="/deposits" element={<Navigate to="/housing?tab=deposits" replace />} />
          <Route path="/services/property" element={<Navigate to="/housing?tab=rooms" replace />} />
          <Route path="/property" element={<Navigate to="/housing?tab=rooms" replace />} />
          <Route path="/services/rooms" element={<Navigate to="/housing?tab=rooms" replace />} />
          <Route path="/rooms" element={<Navigate to="/housing?tab=rooms" replace />} />
          <Route path="/electricity" element={<Navigate to="/services/electricity" replace />} />
          <Route path="/expenses" element={<ExpensesPage />} />
          <Route path="/payments" element={<PaymentsPage />} />
          <Route path="/statements" element={<StatementsPage />} />
          <Route path="/statements/:statementId" element={<StatementDetailPage />} />
          <Route path="/reconciliation" element={<ReconciliationPage />} />
          <Route path="/transactions" element={<TransactionsPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="/setup" element={<SetupPage />} />
          <Route path="/welcome" element={<SetupWizardPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </ToastProvider>
  )
}
