import { useState } from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { PhoneInput } from './PhoneInput'

function Wrapper({ initial = '' }: { initial?: string }) {
  const [value, setValue] = useState(initial)
  return (
    <div>
      <PhoneInput label="Phone number" value={value} onChange={setValue} />
      <output data-testid="value">{value}</output>
    </div>
  )
}

describe('PhoneInput', () => {
  it('defaults to India and produces a full E.164 value as the customer types', async () => {
    const user = userEvent.setup()
    render(<Wrapper />)

    expect(screen.getByLabelText(/country code/i)).toHaveValue('IN')
    await user.type(screen.getByLabelText(/phone number/i), '9876543210')

    expect(screen.getByTestId('value')).toHaveTextContent('+919876543210')
  })

  it('switching the country changes the E.164 country code produced', async () => {
    const user = userEvent.setup()
    render(<Wrapper />)

    await user.selectOptions(screen.getByLabelText(/country code/i), 'FR')
    await user.type(screen.getByLabelText(/phone number/i), '612345678')

    expect(screen.getByTestId('value')).toHaveTextContent('+33612345678')
  })

  it('prefills the country and national number from an existing E.164 value', () => {
    render(<Wrapper initial="+33612345678" />)

    expect(screen.getByLabelText(/country code/i)).toHaveValue('FR')
    expect(screen.getByLabelText(/phone number/i)).toHaveValue('06 12 34 56 78')
  })

  it('offers a worldwide list of countries, not just India', () => {
    render(<Wrapper />)
    const options = screen.getAllByRole('option')
    expect(options.length).toBeGreaterThan(100)
  })
})
