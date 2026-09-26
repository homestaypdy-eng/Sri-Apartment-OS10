import { Suspense, lazy } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import { GuestAuthProvider, useGuestAuth } from "./context/GuestAuthContext";
import { CustomerAuthProvider, useCustomerAuth } from "./context/CustomerAuthContext";
import { RequireAuth, RequireAdmin, RequireNotManager } from "./components/RequireAuth";
import Layout from "./components/Layout";
import LoginPage from "./pages/LoginPage";
import GuestLoginPage from "./pages/GuestLoginPage";
import CustomerLoginPage from "./pages/CustomerLoginPage";
import CustomerSignUpPage from "./pages/CustomerSignUpPage";
import CustomerForgotPasswordPage from "./pages/CustomerForgotPasswordPage";
import CustomerResetPasswordPage from "./pages/CustomerResetPasswordPage";

// Route-level code splitting: each page becomes its own chunk, loaded on
// demand rather than all bundled into one large file up front.
const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const PropertiesPage = lazy(() => import("./pages/PropertiesPage"));
const GuestsPage = lazy(() => import("./pages/GuestsPage"));
const BookingsPage = lazy(() => import("./pages/BookingsPage"));
const InquiriesPage = lazy(() => import("./pages/InquiriesPage"));
const HousekeepingPage = lazy(() => import("./pages/HousekeepingPage"));
const MaintenancePage = lazy(() => import("./pages/MaintenancePage"));
const BillingPage = lazy(() => import("./pages/BillingPage"));
const FinancePage = lazy(() => import("./pages/FinancePage"));
const ReportsPage = lazy(() => import("./pages/ReportsPage"));
const ProfitSimulatorPage = lazy(() => import("./pages/ProfitSimulatorPage"));
const AIStrategistPage = lazy(() => import("./pages/AIStrategistPage"));
const UsersPage = lazy(() => import("./pages/UsersPage"));
const InventoryPage = lazy(() => import("./pages/InventoryPage"));
const StaffPage = lazy(() => import("./pages/StaffPage"));
const StaffOperationsPage = lazy(() => import("./pages/StaffOperationsPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const AuditPage = lazy(() => import("./pages/AuditPage"));
const NotificationsPage = lazy(() => import("./pages/NotificationsPage"));
const MessagesPage = lazy(() => import("./pages/MessagesPage"));
const GuestRequestsPage = lazy(() => import("./pages/GuestRequestsPage"));
const ZotelPage = lazy(() => import("./pages/ZotelPage"));
const PromoCodesPage = lazy(() => import("./pages/PromoCodesPage"));
const GuestDashboardPage = lazy(() => import("./pages/GuestDashboardPage"));
const CustomerAccountPage = lazy(() => import("./pages/CustomerAccountPage"));

function PageLoading() {
  return <div className="p-8 text-sm text-slate-400">Loading...</div>;
}

function RequireGuestAuth({ children }: { children: React.ReactNode }) {
  const { isGuestAuthenticated, isInitializing } = useGuestAuth();
  if (isInitializing) return null;
  if (!isGuestAuthenticated) return <Navigate to="/guest/login" replace />;
  return <>{children}</>;
}

function RequireCustomerAuth({ children }: { children: React.ReactNode }) {
  const { isCustomerAuthenticated, isInitializing } = useCustomerAuth();
  if (isInitializing) return null;
  if (!isCustomerAuthenticated) return <Navigate to="/account/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <AuthProvider>
      <GuestAuthProvider>
        <CustomerAuthProvider>
        <BrowserRouter>
          <Routes>
            {/* Staff/Admin app */}
            <Route path="/login" element={<LoginPage />} />
            <Route
              path="/"
              element={
                <RequireAuth>
                  <Layout />
                </RequireAuth>
              }
            >
              <Route index element={<Suspense fallback={<PageLoading />}><DashboardPage /></Suspense>} />
              <Route path="properties" element={<Suspense fallback={<PageLoading />}><PropertiesPage /></Suspense>} />
              <Route path="guests" element={<Suspense fallback={<PageLoading />}><GuestsPage /></Suspense>} />
              <Route path="bookings" element={<Suspense fallback={<PageLoading />}><BookingsPage /></Suspense>} />
              <Route path="inquiries" element={<Suspense fallback={<PageLoading />}><InquiriesPage /></Suspense>} />
              <Route path="housekeeping" element={<Suspense fallback={<PageLoading />}><HousekeepingPage /></Suspense>} />
              <Route path="maintenance" element={<Suspense fallback={<PageLoading />}><MaintenancePage /></Suspense>} />
              <Route path="inventory" element={<Suspense fallback={<PageLoading />}><InventoryPage /></Suspense>} />
              <Route
                path="staff"
                element={
                  <RequireNotManager>
                    <Suspense fallback={<PageLoading />}>
                      <StaffPage />
                    </Suspense>
                  </RequireNotManager>
                }
              />
              <Route path="operations" element={<Suspense fallback={<PageLoading />}><StaffOperationsPage /></Suspense>} />
              <Route path="guest-requests" element={<Suspense fallback={<PageLoading />}><GuestRequestsPage /></Suspense>} />
              <Route path="billing" element={<Suspense fallback={<PageLoading />}><BillingPage /></Suspense>} />
              <Route path="finance" element={<Suspense fallback={<PageLoading />}><FinancePage /></Suspense>} />
              <Route path="reports" element={<Suspense fallback={<PageLoading />}><ReportsPage /></Suspense>} />
              <Route path="profit-simulator" element={<Suspense fallback={<PageLoading />}><ProfitSimulatorPage /></Suspense>} />
              <Route path="notifications" element={<Suspense fallback={<PageLoading />}><NotificationsPage /></Suspense>} />
              <Route path="messages" element={<Suspense fallback={<PageLoading />}><MessagesPage /></Suspense>} />
              <Route path="promo-codes" element={<Suspense fallback={<PageLoading />}><PromoCodesPage /></Suspense>} />
              <Route
                path="settings"
                element={
                  <RequireNotManager>
                    <Suspense fallback={<PageLoading />}>
                      <SettingsPage />
                    </Suspense>
                  </RequireNotManager>
                }
              />
              <Route
                path="ai-strategist"
                element={
                  <RequireAdmin>
                    <Suspense fallback={<PageLoading />}>
                      <AIStrategistPage />
                    </Suspense>
                  </RequireAdmin>
                }
              />
              <Route
                path="audit"
                element={
                  <RequireAdmin>
                    <Suspense fallback={<PageLoading />}>
                      <AuditPage />
                    </Suspense>
                  </RequireAdmin>
                }
              />
              <Route
                path="users"
                element={
                  <RequireAdmin>
                    <Suspense fallback={<PageLoading />}>
                      <UsersPage />
                    </Suspense>
                  </RequireAdmin>
                }
              />
              <Route
                path="zotel"
                element={
                  <RequireAdmin>
                    <Suspense fallback={<PageLoading />}>
                      <ZotelPage />
                    </Suspense>
                  </RequireAdmin>
                }
              />
            </Route>

            {/* Guest Portal — entirely separate auth, separate layout, no sidebar */}
            <Route path="/guest/login" element={<GuestLoginPage />} />
            <Route
              path="/guest"
              element={
                <RequireGuestAuth>
                  <Suspense fallback={<PageLoading />}>
                    <GuestDashboardPage />
                  </Suspense>
                </RequireGuestAuth>
              }
            />

            {/* Customer Account — a THIRD, separate auth surface (self-
                service Sign Up / Log In), never to be confused with staff
                auth above or Guest Portal auth above. No sidebar. */}
            <Route path="/account/login" element={<CustomerLoginPage />} />
            <Route path="/account/signup" element={<CustomerSignUpPage />} />
            <Route path="/account/forgot-password" element={<CustomerForgotPasswordPage />} />
            <Route path="/account/reset-password" element={<CustomerResetPasswordPage />} />
            <Route
              path="/account"
              element={
                <RequireCustomerAuth>
                  <Suspense fallback={<PageLoading />}>
                    <CustomerAccountPage />
                  </Suspense>
                </RequireCustomerAuth>
              }
            />
          </Routes>
        </BrowserRouter>
        </CustomerAuthProvider>
      </GuestAuthProvider>
    </AuthProvider>
  );
}
