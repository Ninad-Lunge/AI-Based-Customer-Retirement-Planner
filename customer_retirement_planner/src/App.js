import React from 'react';
import { BrowserRouter, Route, Routes, Navigate } from 'react-router-dom';

import Home from './Pages/Home';
import Plan from './Pages/Plan';

import './App.css';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route exact path='/' element={<Home />} />
        <Route exact path='/Home' element={<Home />} />
        <Route exact path='/Plan' element={<Plan />} />
        <Route path='*' element={<Navigate to='/' replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
