/** Logging in, then on to the page that was asked for (the dashboard otherwise). */
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { ErrorNotice } from '../../components/Primitives'
import { TextField } from '../../components/Form'
import { BUTTON_PRIMARY } from '../../components/ui'
import { useAuth } from '../../context/AuthContext'
import { AuthCard, EMAIL_PATTERN } from './AuthCard'

export default function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  const errors = {
    email: !email.trim()
      ? 'Enter your email address.'
      : EMAIL_PATTERN.test(email.trim())
        ? null
        : 'Enter a valid email address, like name@example.com.',
    password: password ? null : 'Enter your password.',
  }
  const valid = !errors.email && !errors.password

  return (
    <AuthCard title="Log in to your Household" subtitle="Use the email and password of your account.">
      <form
        className="stack"
        noValidate
        onSubmit={async (event) => {
          event.preventDefault()
          setSubmitted(true)
          if (!valid || pending) return
          setPending(true)
          setError(null)
          try {
            await login(email, password)
            // Only an address inside the app: never somewhere a link supplied.
            navigate(from && from.startsWith('/') && !from.startsWith('//') ? from : '/', {
              replace: true,
            })
          } catch (cause) {
            setError(cause instanceof ApiError ? cause : null)
            setPending(false)
          }
        }}
      >
        {error && <ErrorNotice error={error} />}
        <TextField
          label="Email address"
          type="email"
          value={email}
          onChange={setEmail}
          required
          maxLength={254}
          autoComplete="email"
          error={submitted ? errors.email : null}
        />
        <TextField
          label="Password"
          type="password"
          value={password}
          onChange={setPassword}
          required
          maxLength={1024}
          autoComplete="current-password"
          error={submitted ? errors.password : null}
        />
        <button type="submit" className={`${BUTTON_PRIMARY} w-full justify-center`} disabled={pending}>
          {pending ? 'Logging in…' : 'Log in'}
        </button>
      </form>
    </AuthCard>
  )
}
